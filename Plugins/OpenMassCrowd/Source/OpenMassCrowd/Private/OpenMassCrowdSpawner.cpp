#include "OpenMassCrowdSpawner.h"

#include "OpenMassCrowdCitySampleActor.h"
#include "OpenMassCrowdTrait.h"
#include "OpenMassCrowdVisualization.h"

#include "Animation/AnimSequence.h"
#include "AnimToTextureDataAsset.h"
#include "AnimToTextureInstancePlaybackHelpers.h"
#include "Avoidance/MassAvoidanceTrait.h"
#include "Avoidance/MassNavigationObstacleTrait.h"
#include "Components/PrimitiveComponent.h"
#include "Components/SceneComponent.h"
#include "Engine/World.h"
#include "Engine/StaticMesh.h"
#include "MassActorSubsystem.h"
#include "MassCommonFragments.h"
#include "MassCrowdFragments.h"
#include "MassCrowdMemberTrait.h"
#include "MassCrowdSubsystem.h"
#include "MassCrowdVisualizationTrait.h"
#include "MassEntityConfigAsset.h"
#include "MassEntityManager.h"
#include "MassLODTrait.h"
#include "MassMovementFragments.h"
#include "MassNavigationFragments.h"
#include "MassRepresentationTypes.h"
#include "MassSpawnerSubsystem.h"
#include "MassZoneGraphNavigationFragments.h"
#include "MassZoneGraphNavigationTrait.h"
#include "MassZoneGraphNavigationTypes.h"
#include "MassZoneGraphNavigationUtils.h"
#include "Movement/MassMovementTrait.h"
#include "Materials/MaterialInterface.h"
#include "Misc/ScopeLock.h"
#include "SmoothOrientation/MassSmoothOrientationTrait.h"
#include "Steering/MassSteeringTrait.h"
#include "TimerManager.h"
#include "UObject/UObjectIterator.h"
#include "ZoneGraphAStar.h"
#include "ZoneGraphData.h"
#include "ZoneGraphQuery.h"
#include "ZoneGraphSubsystem.h"

namespace
{
constexpr int32 MaxGroundRetries = 60;
constexpr float LaneHeightOffset = 2.0f;
constexpr float MaxPedestrianSampleHeightDelta = 55.0f;
// Keep the point-to-point grade certificate consistent with the accepted
// collision-triangle normal. Normal.Z=0.72 corresponds to a 43.9-degree slope
// and tan(43.9 degrees) ~= 0.96. A lower, unrelated grade limit incorrectly
// rejected genuine Cesium walking surfaces that passed the normal test.
constexpr float MinWalkableSurfaceNormalZ = 0.72f;
constexpr float MaxPedestrianSampleGrade = 0.96f;
// A 10 cm support interval is smaller than an adult footprint. A shorter gap
// can be stepped over physically; a larger unsupported gap necessarily fails
// an exact Cesium collision probe instead of becoming a floating lane chord.
constexpr float MaxPedestrianSupportSpacing = 10.0f;
constexpr float PedestrianClearanceRadius = 30.0f;
constexpr float PedestrianClearanceHalfHeight = 82.0f;
constexpr float PedestrianClearanceAboveGround = 14.0f;

/**
 * UE 5.7 declares FZoneGraphBVTree::Build publicly but does not export that
 * symbol from the ZoneGraph DLL. A flat leaf tree is a valid representation
 * for this bounded, at-most-24-lane demo: Query() simply tests every
 * collision-certified quantized bound.
 */
struct FOpenMassCrowdLinearZoneBVTree final : public FZoneGraphBVTree
{
    void BuildLinear(const TArray<FZoneData>& Zones)
    {
        Nodes.Reset();
        Origin = FVector::ZeroVector;
        QuantizationScale = 0.0f;
        if (Zones.IsEmpty())
        {
            return;
        }

        FBox TotalBounds(ForceInit);
        for (const FZoneData& Zone : Zones)
        {
            TotalBounds += Zone.Bounds;
        }
        const float MaxDimension = FMath::Max(1.0f, TotalBounds.GetSize().GetMax());
        QuantizationScale = MaxQuantizedCoord / MaxDimension;
        Origin = TotalBounds.Min;

        Nodes.Reserve(Zones.Num());
        for (int32 ZoneIndex = 0; ZoneIndex < Zones.Num(); ++ZoneIndex)
        {
            FZoneGraphBVNode Node = CalcNodeBounds(Zones[ZoneIndex].Bounds);
            Node.Index = ZoneIndex;
            Nodes.Add(Node);
        }
    }
};

bool IsContinuousPedestrianSample(
    const FVector& PreviousPoint,
    const FVector& CurrentPoint,
    const float ConfiguredSampleSpacing,
    float& OutHorizontalDistance,
    float& OutHeightDelta,
    float& OutGrade)
{
    OutHorizontalDistance = FVector::Dist2D(PreviousPoint, CurrentPoint);
    OutHeightDelta = FMath::Abs(CurrentPoint.Z - PreviousPoint.Z);
    OutGrade = OutHorizontalDistance > KINDA_SMALL_NUMBER
        ? OutHeightDelta / OutHorizontalDistance
        : TNumericLimits<float>::Max();

    return OutHorizontalDistance > KINDA_SMALL_NUMBER &&
        OutHorizontalDistance <= FMath::Max(ConfiguredSampleSpacing, 1.0f) + 1.0f &&
        OutHeightDelta <= MaxPedestrianSampleHeightDelta &&
        OutGrade <= MaxPedestrianSampleGrade;
}

template <typename TTrait>
TTrait* AddRuntimeTrait(
    UObject& Owner,
    FMassEntityConfig& Config,
    TArray<TObjectPtr<UMassEntityTraitBase>>& RuntimeTraits)
{
    TTrait* Trait = NewObject<TTrait>(&Owner);
    RuntimeTraits.Add(Trait);
    Config.AddTrait(*Trait);
    return Trait;
}

void AppendLane(
    FZoneGraphStorage& Storage,
    const TArray<FVector>& Points,
    const float Width,
    const int32 ZoneIndex,
    const TArray<int32>& OutgoingLaneIndices,
    const TArray<int32>& IncomingLaneIndices)
{
    const int32 PointBegin = Storage.LanePoints.Num();
    float Progression = 0.0f;

    for (int32 PointIndex = 0; PointIndex < Points.Num(); ++PointIndex)
    {
        const FVector& Point = Points[PointIndex];
        if (PointIndex > 0)
        {
            Progression += FVector::Distance(Points[PointIndex - 1], Point);
        }

        const int32 PreviousIndex = FMath::Max(PointIndex - 1, 0);
        const int32 NextIndex = FMath::Min(PointIndex + 1, Points.Num() - 1);
        const FVector Tangent = (Points[NextIndex] - Points[PreviousIndex]).GetSafeNormal2D();

        Storage.LanePoints.Add(Point);
        Storage.LaneUpVectors.Add(FVector::UpVector);
        Storage.LaneTangentVectors.Add(Tangent.IsNearlyZero() ? FVector::ForwardVector : Tangent);
        Storage.LanePointProgressions.Add(Progression);
    }

    FZoneLaneData Lane;
    Lane.Width = Width;
    Lane.Tags = FZoneGraphTagMask(1);
    Lane.PointsBegin = PointBegin;
    Lane.PointsEnd = Storage.LanePoints.Num();
    Lane.LinksBegin = Storage.LaneLinks.Num();
    for (const int32 OutgoingLaneIndex : OutgoingLaneIndices)
    {
        Storage.LaneLinks.Add(FZoneLaneLinkData(
            OutgoingLaneIndex,
            EZoneLaneLinkType::Outgoing,
            EZoneLaneLinkFlags::None));
    }
    for (const int32 IncomingLaneIndex : IncomingLaneIndices)
    {
        Storage.LaneLinks.Add(FZoneLaneLinkData(
            IncomingLaneIndex,
            EZoneLaneLinkType::Incoming,
            EZoneLaneLinkFlags::None));
    }
    Lane.LinksEnd = Storage.LaneLinks.Num();
    Lane.ZoneIndex = ZoneIndex;
    Storage.Lanes.Add(Lane);
}
}

AOpenMassCrowdSpawner::AOpenMassCrowdSpawner()
{
    PrimaryActorTick.bCanEverTick = true;
    // Mass movement completes in PostPhysics.  Synchronize the owned visual
    // actors after that phase so we never read entity transforms while the
    // Mass pipeline may still be updating them.
    PrimaryActorTick.TickGroup = TG_PostUpdateWork;

    SceneRoot = CreateDefaultSubobject<USceneComponent>(TEXT("Root"));
    SetRootComponent(SceneRoot);

    FOpenMassCrowdVisualConfig MannequinVisual;
    MannequinVisual.VariantName = TEXT("UE57_AnimToTexture_Mannequin_Temporary");
    MannequinVisual.StaticMesh = TSoftObjectPtr<UStaticMesh>(FSoftObjectPath(
        TEXT("/AnimToTexture/Characters/Mannequin/SM_Mannequin_BoneAnimation.SM_Mannequin_BoneAnimation")));
    MannequinVisual.MaterialOverrides = {
        TSoftObjectPtr<UMaterialInterface>(FSoftObjectPath(
            TEXT("/AnimToTexture/Characters/Mannequin/Materials/BoneAnimation/MI_Body_BoneAnimation.MI_Body_BoneAnimation"))),
        TSoftObjectPtr<UMaterialInterface>(FSoftObjectPath(
            TEXT("/AnimToTexture/Characters/Mannequin/Materials/BoneAnimation/MI_ChestLog_BoneAnimation.MI_ChestLog_BoneAnimation")))
    };
    MannequinVisual.AnimationData = TSoftObjectPtr<UAnimToTextureDataAsset>(FSoftObjectPath(
        TEXT("/AnimToTexture/Characters/Mannequin/Data/DA_BoneAnimation.DA_BoneAnimation")));
    MannequinVisual.AnimationSequence = TSoftObjectPtr<UAnimSequence>(FSoftObjectPath(
        TEXT("/AnimToTexture/Characters/Mannequin/Animations/Walk_Fwd.Walk_Fwd")));
    MannequinVisual.LocalTransform = FTransform(FRotator(0.0, -90.0, 0.0));
    VisualVariants.Add(MoveTemp(MannequinVisual));

    Tags.Add(TEXT("HK_OpenMass_Crowd_Demo"));
}

void AOpenMassCrowdSpawner::ConfigureOfficialCitySampleVisual()
{
    VisualVariants.Reset();

    FOpenMassCrowdVisualConfig OfficialCitySampleVisual;
    OfficialCitySampleVisual.VariantName = TEXT("Epic_CitySampleCrowd_Official");
    OfficialCitySampleVisual.bUseActorRepresentation = true;
    OfficialCitySampleVisual.HighResTemplateActor = AOpenMassCrowdCitySampleActor::StaticClass();
    OfficialCitySampleVisual.LowResTemplateActor = AOpenMassCrowdCitySampleLowResActor::StaticClass();
    OfficialCitySampleVisual.LocalTransform = FTransform::Identity;
    OfficialCitySampleVisual.bCastShadows = true;
    VisualVariants.Add(MoveTemp(OfficialCitySampleVisual));

    UE_LOG(
        LogTemp,
        Warning,
        TEXT("OPEN_MASS_CITY_SAMPLE_VISUAL_CONFIGURED variants=%d population=%d"),
        VisualVariants.Num(),
        PopulationCount);
}

void AOpenMassCrowdSpawner::BeginPlay()
{
    Super::BeginPlay();

    if (bSpawnOnBeginPlay)
    {
        GetWorldTimerManager().SetTimer(
            SpawnRetryTimer,
            this,
            &AOpenMassCrowdSpawner::RetrySpawn,
            1.5f,
            false);
    }
}

void AOpenMassCrowdSpawner::EndPlay(const EEndPlayReason::Type EndPlayReason)
{
    GetWorldTimerManager().ClearTimer(SpawnRetryTimer);
    DestroyRuntimePopulation();
    Super::EndPlay(EndPlayReason);
}

void AOpenMassCrowdSpawner::Tick(const float DeltaSeconds)
{
    Super::Tick(DeltaSeconds);

    if (SpawnedEntities.IsEmpty())
    {
        return;
    }

    // A ZoneGraph short path can be partial (its fixed point buffer is smaller
    // than a sampled lane). Continue it on the first PostUpdateWork tick in
    // which Mass marks it done so pedestrians do not pause for a polling timer.
    PathRefreshAccumulator = 0.0f;
    RefreshCompletedPaths();

    GroundCorrectionAccumulator += DeltaSeconds;
    // Avoidance can move an entity laterally across a certified sloped
    // corridor. Cap correction at 20 Hz so its visible root continues to follow
    // the exact Cesium surface rather than holding the centre-lane Z for 0.2 s.
    const float EffectiveGroundCorrectionInterval = FMath::Min(
        GroundCorrectionInterval,
        0.05f);
    if (GroundCorrectionAccumulator >= EffectiveGroundCorrectionInterval)
    {
        GroundCorrectionAccumulator = 0.0f;
        CorrectMassGrounding();
    }

    // Spawned actor representations are positioned when Mass creates or swaps
    // them, but UE 5.7 does not continuously copy FTransformFragment back to a
    // plain AActor.  Our official City Sample character is a child of that
    // lightweight actor, so keep the visible 30-person demo in lock-step with
    // the Mass/ZoneGraph simulation.
    SyncVisualActorsToMass();
}

bool AOpenMassCrowdSpawner::ProjectToCesiumGround(
    const FVector& XYPoint,
    FVector& OutGroundPoint) const
{
    if (!GetWorld())
    {
        return false;
    }

    // XYPoint.Z is the locally expected elevation.  Initial probes pass the
    // spawner Z, while lane and correction probes pass their previous/current
    // grounded Z so a walkable hill can accumulate beyond GroundTolerance.
    const float ExpectedElevation = XYPoint.Z;
    const auto IsAcceptedCesiumHit = [this, ExpectedElevation](const FHitResult& Hit)
    {
        const UPrimitiveComponent* Component = Hit.GetComponent();
        const bool bCesiumComponent = Component &&
            Component->GetClass()->GetName().Contains(TEXT("CesiumGltfPrimitiveComponent"));
        const bool bWalkableSlope =
            Hit.ImpactNormal.Z >= MinWalkableSurfaceNormalZ;
        const bool bExpectedElevation =
            FMath::Abs(Hit.ImpactPoint.Z - ExpectedElevation) <= GroundTolerance;

        return bCesiumComponent && bWalkableSlope && bExpectedElevation;
    };

    // The lane point itself must own a real collision triangle. Do not borrow
    // elevation from a neighbouring footprint probe: that produces a valid Z
    // at an unsupported XY and is exactly the old floating-path failure.
    const FVector Start(
        XYPoint.X,
        XYPoint.Y,
        ExpectedElevation + TraceHeight);
    const FVector End(
        XYPoint.X,
        XYPoint.Y,
        ExpectedElevation - TraceDepth);
    FCollisionQueryParams Params(SCENE_QUERY_STAT(OpenMassCrowdGround), true, this);
    Params.AddIgnoredActor(this);

    // Select one global raw first blocker across the world response channel and
    // direct Cesium component queries. Some streamed Cesium primitives do not
    // participate in the selected channel, while a world blocker may still be
    // closer than a direct component hit. Comparing every raw hit before any
    // qualification prevents either query path from looking through the other.
    bool bFoundRawHit = false;
    float NearestDistanceSquared = TNumericLimits<float>::Max();
    FHitResult NearestHit;
    FHitResult WorldHit;
    if (GetWorld()->LineTraceSingleByChannel(
            WorldHit,
            Start,
            End,
            ECC_Visibility,
            Params))
    {
        NearestDistanceSquared = FVector::DistSquared(Start, WorldHit.ImpactPoint);
        NearestHit = WorldHit;
        bFoundRawHit = true;
    }

    // Query Cesium components directly at the same exact XY, then compare each
    // raw component hit with the world hit instead of allowing either source to
    // return early.
    for (TObjectIterator<UPrimitiveComponent> It; It; ++It)
    {
        UPrimitiveComponent* Component = *It;
        if (!IsValid(Component) || Component->GetWorld() != GetWorld() ||
            !Component->IsRegistered() || !Component->IsVisible() ||
            !Component->IsQueryCollisionEnabled() ||
            !Component->GetClass()->GetName().Contains(TEXT("CesiumGltfPrimitiveComponent")))
        {
            continue;
        }

        FHitResult ComponentHit;
        if (Component->LineTraceComponent(ComponentHit, Start, End, Params))
        {
            const float DistanceSquared = FVector::DistSquared(Start, ComponentHit.ImpactPoint);
            if (DistanceSquared < NearestDistanceSquared)
            {
                NearestDistanceSquared = DistanceSquared;
                NearestHit = ComponentHit;
                bFoundRawHit = true;
            }
        }
    }

    // Apply Cesium ownership, walkability and elevation checks only after
    // selecting the global raw first blocker. Skipping a closer wall/roof and
    // accepting a hidden flat triangle below it would be collision tunnelling.
    if (bFoundRawHit && IsAcceptedCesiumHit(NearestHit))
    {
        OutGroundPoint = FVector(
            XYPoint.X,
            XYPoint.Y,
            NearestHit.ImpactPoint.Z);
        return true;
    }

    return false;
}

bool AOpenMassCrowdSpawner::HasPedestrianClearance(
    const FVector& StartGroundedPoint,
    const FVector& EndGroundedPoint) const
{
    if (!GetWorld())
    {
        return false;
    }

    // Sweep an upright pedestrian volume continuously between adjacent,
    // collision-grounded samples. Exact support probes reject gaps; this sweep
    // independently rejects walls, parapets and other obstructions between the
    // samples. A small bottom clearance tolerates photogrammetry roughness.
    const float CenterHeight =
        PedestrianClearanceHalfHeight + PedestrianClearanceAboveGround;
    const FVector Start = StartGroundedPoint + FVector(0.0, 0.0, CenterHeight);
    const FVector End = EndGroundedPoint + FVector(0.0, 0.0, CenterHeight);
    const FCollisionShape PedestrianShape = FCollisionShape::MakeCapsule(
        PedestrianClearanceRadius,
        PedestrianClearanceHalfHeight);
    FBox DirectSweepBounds(ForceInit);
    DirectSweepBounds += Start;
    DirectSweepBounds += End;
    DirectSweepBounds = DirectSweepBounds.ExpandBy(FVector(
        PedestrianClearanceRadius,
        PedestrianClearanceRadius,
        PedestrianClearanceHalfHeight));
    FCollisionQueryParams Params(SCENE_QUERY_STAT(OpenMassCrowdClearance), true, this);
    Params.AddIgnoredActor(this);

    FHitResult Hit;
    if (!GetWorld()->SweepSingleByChannel(
            Hit,
            Start,
            End,
            FQuat::Identity,
            ECC_Visibility,
            PedestrianShape,
            Params))
    {
        // Match ProjectToCesiumGround's fallback semantics. Some streamed
        // Cesium primitives are query-enabled yet do not block the selected
        // world channel, so certify the same capsule against each primitive
        // directly before calling this corridor clear.
        for (TObjectIterator<UPrimitiveComponent> It; It; ++It)
        {
            UPrimitiveComponent* Component = *It;
            if (!IsValid(Component) || Component->GetWorld() != GetWorld() ||
                !Component->IsRegistered() || !Component->IsVisible() ||
                !Component->IsQueryCollisionEnabled() ||
                !Component->Bounds.GetBox().Intersect(DirectSweepBounds) ||
                !Component->GetClass()->GetName().Contains(TEXT("CesiumGltfPrimitiveComponent")))
            {
                continue;
            }

            FHitResult ComponentHit;
            if (Component->SweepComponent(
                    ComponentHit,
                    Start,
                    End,
                    FQuat::Identity,
                    PedestrianShape,
                    true))
            {
                return false;
            }
        }
        return true;
    }

    const UPrimitiveComponent* Component = Hit.GetComponent();
    UE_LOG(
        LogTemp,
        Warning,
        TEXT("OPEN_MASS_CROWD_CLEARANCE_BLOCKED component=%s normal_z=%.3f impact_z=%.1f"),
        *GetNameSafe(Component),
        Hit.ImpactNormal.Z,
        Hit.ImpactPoint.Z);
    return false;
}

bool AOpenMassCrowdSpawner::HasPedestrianCorridorSupport(
    const FVector& StartGroundedPoint,
    const FVector& EndGroundedPoint) const
{
    if (!HasPedestrianClearance(StartGroundedPoint, EndGroundedPoint))
    {
        UE_LOG(
            LogTemp,
            Warning,
            TEXT("OPEN_MASS_CROWD_CORRIDOR_REJECT reason=center_clearance"));
        return false;
    }

    const FVector Direction =
        (EndGroundedPoint - StartGroundedPoint).GetSafeNormal2D();
    if (Direction.IsNearlyZero())
    {
        UE_LOG(
            LogTemp,
            Warning,
            TEXT("OPEN_MASS_CROWD_CORRIDOR_REJECT reason=zero_direction"));
        return false;
    }

    // Mass avoidance may move the agent away from the lane centre. Certify two
    // additional longitudinal tracks at the extreme legal agent-centre
    // positions. At every longitudinal station, also walk from the centre to
    // each side in <=10 cm exact-XY steps. This rejects a hidden transverse
    // gap or vertical step instead of inferring support from two endpoints.
    const float EffectiveLaneWidth = FMath::Clamp(LaneWidth, 120.0f, 160.0f);
    const float SideOffset = FMath::Max(
        0.0f,
        EffectiveLaneWidth * 0.5f - PedestrianClearanceRadius);
    const FVector Right(-Direction.Y, Direction.X, 0.0f);
    const auto BuildCertifiedCrossSection =
        [this, &Right, SideOffset](
            const FVector& CenterPoint,
            const float SideSign,
            const TCHAR* StationName,
            FVector& OutSidePoint)
        {
            if (SideOffset <= KINDA_SMALL_NUMBER)
            {
                OutSidePoint = CenterPoint;
                return true;
            }

            const int32 CrossSampleCount = FMath::Max(
                1,
                FMath::CeilToInt(
                    SideOffset / MaxPedestrianSupportSpacing));
            FVector PreviousPoint = CenterPoint;
            for (int32 CrossIndex = 1;
                 CrossIndex <= CrossSampleCount;
                 ++CrossIndex)
            {
                const float Alpha =
                    static_cast<float>(CrossIndex) /
                    static_cast<float>(CrossSampleCount);
                const float CrossOffset = SideOffset * Alpha;
                FVector DesiredPoint =
                    CenterPoint + Right * CrossOffset * SideSign;
                DesiredPoint.Z = PreviousPoint.Z - LaneHeightOffset;

                FVector GroundPoint;
                if (!ProjectToCesiumGround(DesiredPoint, GroundPoint))
                {
                    UE_LOG(
                        LogTemp,
                        Warning,
                        TEXT("OPEN_MASS_CROWD_CORRIDOR_REJECT reason=cross_ground station=%s side=%+.0f step=%d/%d offset_cm=%.1f"),
                        StationName,
                        SideSign,
                        CrossIndex,
                        CrossSampleCount,
                        CrossOffset);
                    return false;
                }

                const FVector CurrentPoint =
                    GroundPoint + FVector(0.0, 0.0, LaneHeightOffset);
                float HorizontalDistance = 0.0f;
                float HeightDelta = 0.0f;
                float Grade = 0.0f;
                if (!IsContinuousPedestrianSample(
                        PreviousPoint,
                        CurrentPoint,
                        MaxPedestrianSupportSpacing,
                        HorizontalDistance,
                        HeightDelta,
                        Grade))
                {
                    UE_LOG(
                        LogTemp,
                        Warning,
                        TEXT("OPEN_MASS_CROWD_CORRIDOR_REJECT reason=cross_continuity station=%s side=%+.0f step=%d/%d horizontal=%.1f dz=%.1f grade=%.3f"),
                        StationName,
                        SideSign,
                        CrossIndex,
                        CrossSampleCount,
                        HorizontalDistance,
                        HeightDelta,
                        Grade);
                    return false;
                }
                if (!HasPedestrianClearance(PreviousPoint, CurrentPoint))
                {
                    UE_LOG(
                        LogTemp,
                        Warning,
                        TEXT("OPEN_MASS_CROWD_CORRIDOR_REJECT reason=cross_clearance station=%s side=%+.0f step=%d/%d"),
                        StationName,
                        SideSign,
                        CrossIndex,
                        CrossSampleCount);
                    return false;
                }

                PreviousPoint = CurrentPoint;
            }

            OutSidePoint = PreviousPoint;
            return true;
        };

    for (const float SideSign : {-1.0f, 1.0f})
    {
        FVector SideStart;
        FVector SideEnd;
        if (!BuildCertifiedCrossSection(
                StartGroundedPoint,
                SideSign,
                TEXT("start"),
                SideStart) ||
            !BuildCertifiedCrossSection(
                EndGroundedPoint,
                SideSign,
                TEXT("end"),
                SideEnd))
        {
            return false;
        }
        float HorizontalDistance = 0.0f;
        float HeightDelta = 0.0f;
        float Grade = 0.0f;
        if (!IsContinuousPedestrianSample(
                SideStart,
                SideEnd,
                MaxPedestrianSupportSpacing,
                HorizontalDistance,
                HeightDelta,
                Grade))
        {
            UE_LOG(
                LogTemp,
                Warning,
                TEXT("OPEN_MASS_CROWD_CORRIDOR_REJECT reason=side_continuity side=%+.0f horizontal=%.1f dz=%.1f grade=%.3f"),
                SideSign,
                HorizontalDistance,
                HeightDelta,
                Grade);
            return false;
        }
        if (!HasPedestrianClearance(SideStart, SideEnd))
        {
            UE_LOG(
                LogTemp,
                Warning,
                TEXT("OPEN_MASS_CROWD_CORRIDOR_REJECT reason=side_clearance side=%+.0f"),
                SideSign);
            return false;
        }
    }

    return true;
}

bool AOpenMassCrowdSpawner::BuildGroundedRoute(TArray<FVector>& OutRoutePoints) const
{
    const float X = RouteHalfExtent.X;
    const float Y = RouteHalfExtent.Y;
    // A compact 3x3 pedestrian grid.  The corners and edge midpoints retain
    // the proven footprint of the original loop, while the center and spokes
    // introduce real routing decisions at five branching intersections.
    const TArray<FVector2D> RelativePoints = {
        FVector2D(-X, -Y), FVector2D(0.0f, -Y), FVector2D(X, -Y),
        FVector2D(-X, 0.0f), FVector2D(0.0f, 0.0f), FVector2D(X, 0.0f),
        FVector2D(-X, Y), FVector2D(0.0f, Y), FVector2D(X, Y)
    };

    OutRoutePoints.SetNum(RelativePoints.Num());

    // Anchor the network at the center using the spawner elevation.  All other
    // nodes are reached through the bounded 3x3 spanning tree below, probing at
    // no more than a footprint-scale support interval and carrying the previous
    // accepted Z forward.
    FVector CenterGroundPoint;
    if (!ProjectToCesiumGround(GetActorLocation(), CenterGroundPoint))
    {
        OutRoutePoints.Reset();
        return false;
    }
    OutRoutePoints[4] = CenterGroundPoint + FVector(0.0, 0.0, LaneHeightOffset);

    struct FNodeProjection
    {
        int32 ParentNode = INDEX_NONE;
        int32 ChildNode = INDEX_NONE;
    };
    static const FNodeProjection ProjectionOrder[] = {
        {4, 3}, {4, 5}, {4, 1}, {4, 7},
        {3, 0}, {3, 6}, {5, 2}, {5, 8}
    };

    for (const FNodeProjection& Projection : ProjectionOrder)
    {
        const FVector StartPoint = OutRoutePoints[Projection.ParentNode];
        const FVector2D& ChildRelativePoint = RelativePoints[Projection.ChildNode];
        const FVector ChildXY = GetActorLocation() +
            FVector(ChildRelativePoint.X, ChildRelativePoint.Y, 0.0f);
        const float SegmentLength2D = FVector::Dist2D(StartPoint, ChildXY);
        const float EffectiveGroundSpacing = FMath::Min(
            FMath::Max(NetworkGroundSampleSpacing, 1.0f),
            MaxPedestrianSupportSpacing);
        const int32 SegmentCount = FMath::Max(
            1,
            FMath::CeilToInt(SegmentLength2D / EffectiveGroundSpacing));

        FVector PreviousPoint = StartPoint;
        for (int32 SegmentIndex = 1; SegmentIndex <= SegmentCount; ++SegmentIndex)
        {
            const float Alpha =
                static_cast<float>(SegmentIndex) / static_cast<float>(SegmentCount);
            FVector Candidate = FMath::Lerp(StartPoint, ChildXY, Alpha);
            Candidate.Z = PreviousPoint.Z - LaneHeightOffset;

            FVector GroundPoint;
            if (!ProjectToCesiumGround(Candidate, GroundPoint))
            {
                OutRoutePoints.Reset();
                return false;
            }

            const FVector CurrentPoint =
                GroundPoint + FVector(0.0, 0.0, LaneHeightOffset);
            float HorizontalDistance = 0.0f;
            float HeightDelta = 0.0f;
            float Grade = 0.0f;
            if (!IsContinuousPedestrianSample(
                    PreviousPoint,
                    CurrentPoint,
                    EffectiveGroundSpacing,
                    HorizontalDistance,
                    HeightDelta,
                    Grade) ||
                !HasPedestrianClearance(PreviousPoint, CurrentPoint))
            {
                UE_LOG(
                    LogTemp,
                    Warning,
                    TEXT("OPEN_MASS_CROWD_WAIT_ROUTE_CONTINUITY edge=%d->%d sample=%d/%d horizontal=%.1f dz=%.1f grade=%.3f"),
                    Projection.ParentNode,
                    Projection.ChildNode,
                    SegmentIndex,
                    SegmentCount,
                    HorizontalDistance,
                    HeightDelta,
                    Grade);
                OutRoutePoints.Reset();
                return false;
            }
            PreviousPoint = CurrentPoint;
        }

        OutRoutePoints[Projection.ChildNode] = PreviousPoint;
    }

    return true;
}

bool AOpenMassCrowdSpawner::BuildRuntimeZoneGraph(const TArray<FVector>& GroundedRoute)
{
    UZoneGraphSubsystem* ZoneGraphSubsystem =
        UWorld::GetSubsystem<UZoneGraphSubsystem>(GetWorld());
    if (!ZoneGraphSubsystem || GroundedRoute.Num() != 9)
    {
        return false;
    }

    RuntimeZoneGraphData = GetWorld()->SpawnActor<AZoneGraphData>(
        AZoneGraphData::StaticClass(),
        FTransform::Identity);
    if (!RuntimeZoneGraphData)
    {
        return false;
    }

    // Spawned ZoneGraphData registers empty in PostActorCreated. Fill it, then
    // re-register so MassCrowd receives the completed lane storage.
    ZoneGraphSubsystem->UnregisterZoneGraphData(*RuntimeZoneGraphData);

    FScopeLock StorageLock(&RuntimeZoneGraphData->GetStorageLock());
    FZoneGraphStorage& Storage = RuntimeZoneGraphData->GetStorageMutable();
    Storage.Reset();
    const float EffectiveLaneWidth = FMath::Clamp(LaneWidth, 120.0f, 160.0f);

    struct FDirectedLaneBuildData
    {
        int32 StartNode = INDEX_NONE;
        int32 EndNode = INDEX_NONE;
        TArray<FVector> Points;
    };

    // Horizontal and vertical links of the 3x3 grid.  Each undirected edge is
    // materialized as two directed lanes so pedestrians can take independent
    // A-to-B routes in both directions.
    static const FIntPoint UndirectedEdges[] = {
        FIntPoint(0, 1), FIntPoint(1, 2),
        FIntPoint(3, 4), FIntPoint(4, 5),
        FIntPoint(6, 7), FIntPoint(7, 8),
        FIntPoint(0, 3), FIntPoint(3, 6),
        FIntPoint(1, 4), FIntPoint(4, 7),
        FIntPoint(2, 5), FIntPoint(5, 8)
    };

    TArray<FDirectedLaneBuildData> DirectedLanes;
    DirectedLanes.Reserve(UE_ARRAY_COUNT(UndirectedEdges) * 2);
    TArray<int32> NodeDegrees;
    NodeDegrees.Init(0, GroundedRoute.Num());
    TArray<TArray<int32>> NodeAdjacency;
    NodeAdjacency.SetNum(GroundedRoute.Num());
    int32 RejectedEdgeCount = 0;
    for (const FIntPoint& Edge : UndirectedEdges)
    {
        const FVector& StartNode = GroundedRoute[Edge.X];
        const FVector& EndNode = GroundedRoute[Edge.Y];
        const float SegmentLength2D = FVector::Dist2D(StartNode, EndNode);
        const float EffectiveGroundSpacing = FMath::Min(
            FMath::Max(NetworkGroundSampleSpacing, 1.0f),
            MaxPedestrianSupportSpacing);
        const FVector EdgeDirection =
            (EndNode - StartNode).GetSafeNormal2D();
        if (EdgeDirection.IsNearlyZero())
        {
            ++RejectedEdgeCount;
            continue;
        }
        const FVector EdgeRight(
            -EdgeDirection.Y,
            EdgeDirection.X,
            0.0f);

        // A fixed straight grid edge may meet a real facade or gap. Try a
        // bounded raised-cosine dogleg that leaves both junction positions and
        // tangents unchanged. Every candidate is independently certified; an
        // offset is never accepted merely because the straight edge failed.
        constexpr float DetourRampFraction = 0.25f;
        constexpr float MaxDetourOffset = 60.0f;
        const auto TryCertifiedCandidate =
            [this,
             &Edge,
             &StartNode,
             &EndNode,
             &EdgeRight,
             SegmentLength2D,
             EffectiveGroundSpacing](
                const float LateralOffset,
                TArray<FVector>& OutPoints)
            {
                const float MaxLateralDerivative =
                    FMath::Abs(LateralOffset) * UE_PI /
                    (2.0f * DetourRampFraction);
                const float CertifiedLengthUpperBound = FMath::Sqrt(
                    FMath::Square(SegmentLength2D) +
                    FMath::Square(MaxLateralDerivative));
                const int32 SegmentCount = FMath::Max(
                    1,
                    FMath::CeilToInt(
                        CertifiedLengthUpperBound /
                        EffectiveGroundSpacing));

                OutPoints.Reset(SegmentCount + 1);
                OutPoints.Add(StartNode);
                for (int32 SegmentIndex = 1;
                     SegmentIndex < SegmentCount;
                     ++SegmentIndex)
                {
                    const float Alpha =
                        static_cast<float>(SegmentIndex) /
                        static_cast<float>(SegmentCount);
                    float DetourWeight = 1.0f;
                    if (Alpha < DetourRampFraction)
                    {
                        DetourWeight = 0.5f - 0.5f * FMath::Cos(
                            UE_PI * Alpha / DetourRampFraction);
                    }
                    else if (Alpha > 1.0f - DetourRampFraction)
                    {
                        DetourWeight = 0.5f - 0.5f * FMath::Cos(
                            UE_PI * (1.0f - Alpha) /
                            DetourRampFraction);
                    }

                    FVector XYPoint =
                        FMath::Lerp(StartNode, EndNode, Alpha) +
                        EdgeRight * LateralOffset * DetourWeight;
                    XYPoint.Z = OutPoints.Last().Z - LaneHeightOffset;
                    FVector GroundPoint;
                    if (!ProjectToCesiumGround(XYPoint, GroundPoint))
                    {
                        UE_LOG(
                            LogTemp,
                            Warning,
                            TEXT("OPEN_MASS_CROWD_CANDIDATE_REJECT edge=%d->%d offset_cm=%.1f sample=%d/%d reason=center_ground"),
                            Edge.X,
                            Edge.Y,
                            LateralOffset,
                            SegmentIndex,
                            SegmentCount);
                        return false;
                    }

                    const FVector CurrentPoint =
                        GroundPoint + FVector(
                            0.0,
                            0.0,
                            LaneHeightOffset);
                    float HorizontalDistance = 0.0f;
                    float HeightDelta = 0.0f;
                    float Grade = 0.0f;
                    if (!IsContinuousPedestrianSample(
                            OutPoints.Last(),
                            CurrentPoint,
                            EffectiveGroundSpacing,
                            HorizontalDistance,
                            HeightDelta,
                            Grade) ||
                        !HasPedestrianCorridorSupport(
                            OutPoints.Last(),
                            CurrentPoint))
                    {
                        UE_LOG(
                            LogTemp,
                            Warning,
                            TEXT("OPEN_MASS_CROWD_CANDIDATE_REJECT edge=%d->%d offset_cm=%.1f sample=%d/%d reason=continuity_or_corridor horizontal=%.1f dz=%.1f grade=%.3f"),
                            Edge.X,
                            Edge.Y,
                            LateralOffset,
                            SegmentIndex,
                            SegmentCount,
                            HorizontalDistance,
                            HeightDelta,
                            Grade);
                        return false;
                    }
                    OutPoints.Add(CurrentPoint);
                }

                float FinalHorizontalDistance = 0.0f;
                float FinalHeightDelta = 0.0f;
                float FinalGrade = 0.0f;
                if (!IsContinuousPedestrianSample(
                        OutPoints.Last(),
                        EndNode,
                        EffectiveGroundSpacing,
                        FinalHorizontalDistance,
                        FinalHeightDelta,
                        FinalGrade) ||
                    !HasPedestrianCorridorSupport(
                        OutPoints.Last(),
                        EndNode))
                {
                    UE_LOG(
                        LogTemp,
                        Warning,
                        TEXT("OPEN_MASS_CROWD_CANDIDATE_REJECT edge=%d->%d offset_cm=%.1f sample=%d/%d reason=endpoint horizontal=%.1f dz=%.1f grade=%.3f"),
                        Edge.X,
                        Edge.Y,
                        LateralOffset,
                        SegmentCount,
                        SegmentCount,
                        FinalHorizontalDistance,
                        FinalHeightDelta,
                        FinalGrade);
                    return false;
                }

                OutPoints.Add(EndNode);
                return true;
            };

        const float SideOffset = FMath::Max(
            0.0f,
            EffectiveLaneWidth * 0.5f - PedestrianClearanceRadius);
        TArray<float> CandidateOffsets;
        CandidateOffsets.Add(0.0f);
        if (SideOffset > KINDA_SMALL_NUMBER)
        {
            CandidateOffsets.Add(SideOffset);
            CandidateOffsets.Add(-SideOffset);
            const float WiderOffset = FMath::Min(
                SideOffset * 2.0f,
                MaxDetourOffset);
            CandidateOffsets.AddUnique(WiderOffset);
            CandidateOffsets.AddUnique(-WiderOffset);
        }

        TArray<FVector> ForwardPoints;
        float AcceptedOffset = 0.0f;
        bool bEdgeCertified = false;
        for (const float CandidateOffset : CandidateOffsets)
        {
            if (TryCertifiedCandidate(CandidateOffset, ForwardPoints))
            {
                AcceptedOffset = CandidateOffset;
                bEdgeCertified = true;
                break;
            }
        }

        if (!bEdgeCertified)
        {
            ++RejectedEdgeCount;
            UE_LOG(
                LogTemp,
                Warning,
                TEXT("OPEN_MASS_CROWD_EDGE_REJECTED edge=%d->%d candidates=%d"),
                Edge.X,
                Edge.Y,
                CandidateOffsets.Num());
            continue;
        }
        if (!FMath::IsNearlyZero(AcceptedOffset))
        {
            UE_LOG(
                LogTemp,
                Warning,
                TEXT("OPEN_MASS_CROWD_DETOUR edge=%d->%d offset_cm=%.1f samples=%d"),
                Edge.X,
                Edge.Y,
                AcceptedOffset,
                ForwardPoints.Num());
        }

        FDirectedLaneBuildData& ForwardLane = DirectedLanes.AddDefaulted_GetRef();
        ForwardLane.StartNode = Edge.X;
        ForwardLane.EndNode = Edge.Y;
        ForwardLane.Points = ForwardPoints;

        FDirectedLaneBuildData& ReverseLane = DirectedLanes.AddDefaulted_GetRef();
        ReverseLane.StartNode = Edge.Y;
        ReverseLane.EndNode = Edge.X;
        ReverseLane.Points.Reserve(ForwardPoints.Num());
        for (int32 PointIndex = ForwardPoints.Num() - 1; PointIndex >= 0; --PointIndex)
        {
            ReverseLane.Points.Add(ForwardPoints[PointIndex]);
        }

        ++NodeDegrees[Edge.X];
        ++NodeDegrees[Edge.Y];
        NodeAdjacency[Edge.X].Add(Edge.Y);
        NodeAdjacency[Edge.Y].Add(Edge.X);
    }

    // During Cesium startup, a collision tile can be briefly absent. Give the
    // complete 24-lane grid five retries before accepting a smaller topology.
    // Afterwards, keep only collision-certified edges, exactly as a navmesh
    // excludes a real hole or obstruction; never fabricate support to preserve
    // a requested lane count.
    if (RejectedEdgeCount > 0 && GroundRetryCount < 5)
    {
        UE_LOG(
            LogTemp,
            Warning,
            TEXT("OPEN_MASS_CROWD_WAIT_COMPLETE_NETWORK rejected_edges=%d retry=%d/5"),
            RejectedEdgeCount,
            GroundRetryCount);
        return false;
    }

    TArray<bool> VisitedNodes;
    VisitedNodes.Init(false, GroundedRoute.Num());
    TArray<int32> PendingNodes;
    PendingNodes.Add(0);
    VisitedNodes[0] = true;
    for (int32 PendingIndex = 0;
         PendingIndex < PendingNodes.Num();
         ++PendingIndex)
    {
        const int32 NodeIndex = PendingNodes[PendingIndex];
        for (const int32 NeighbourIndex : NodeAdjacency[NodeIndex])
        {
            if (!VisitedNodes[NeighbourIndex])
            {
                VisitedNodes[NeighbourIndex] = true;
                PendingNodes.Add(NeighbourIndex);
            }
        }
    }

    const int32 ConnectedNodeCount = PendingNodes.Num();
    const int32 MinimumDirectedLaneCount =
        (GroundedRoute.Num() - 1) * 2;
    if (ConnectedNodeCount != GroundedRoute.Num() ||
        DirectedLanes.Num() < MinimumDirectedLaneCount)
    {
        UE_LOG(
            LogTemp,
            Warning,
            TEXT("OPEN_MASS_CROWD_WAIT_CONNECTED_NETWORK nodes=%d/%d lanes=%d minimum_lanes=%d rejected_edges=%d"),
            ConnectedNodeCount,
            GroundedRoute.Num(),
            DirectedLanes.Num(),
            MinimumDirectedLaneCount,
            RejectedEdgeCount);
        return false;
    }

    if (RejectedEdgeCount > 0)
    {
        UE_LOG(
            LogTemp,
            Warning,
            TEXT("OPEN_MASS_CROWD_NETWORK_PRUNED accepted_edges=%d rejected_edges=%d connected_nodes=%d"),
            DirectedLanes.Num() / 2,
            RejectedEdgeCount,
            ConnectedNodeCount);
    }

    FBox RouteBounds(ForceInit);
    for (int32 LaneIndex = 0; LaneIndex < DirectedLanes.Num(); ++LaneIndex)
    {
        const FDirectedLaneBuildData& LaneBuild = DirectedLanes[LaneIndex];
        TArray<int32> OutgoingLaneIndices;
        TArray<int32> IncomingLaneIndices;
        for (int32 CandidateIndex = 0; CandidateIndex < DirectedLanes.Num(); ++CandidateIndex)
        {
            const FDirectedLaneBuildData& Candidate = DirectedLanes[CandidateIndex];
            // An exact reverse would make a pedestrian reach a junction and
            // immediately turn 180 degrees onto the same physical segment.
            // Omit it at a junction, but retain it at a real collision-pruned
            // dead end so a pedestrian can turn around instead of becoming
            // permanently trapped there.
            if (Candidate.StartNode == LaneBuild.EndNode &&
                (Candidate.EndNode != LaneBuild.StartNode ||
                 NodeDegrees[LaneBuild.EndNode] <= 1))
            {
                OutgoingLaneIndices.Add(CandidateIndex);
            }
            if (Candidate.EndNode == LaneBuild.StartNode &&
                (Candidate.StartNode != LaneBuild.EndNode ||
                 NodeDegrees[LaneBuild.StartNode] <= 1))
            {
                IncomingLaneIndices.Add(CandidateIndex);
            }
        }

        // FZoneGraphPathFilter in UE 5.7 only computes general traversal cost
        // across different zones. Giving every directed segment its own zone
        // makes the official FZoneGraphAStar valid for arbitrary graph hops.
        AppendLane(
            Storage,
            LaneBuild.Points,
            EffectiveLaneWidth,
            LaneIndex,
            OutgoingLaneIndices,
            IncomingLaneIndices);

        FZoneData Zone;
        Zone.BoundaryPointsBegin = Storage.BoundaryPoints.Num();
        const float HalfLaneWidth = EffectiveLaneWidth * 0.5f;
        for (int32 PointIndex = 0; PointIndex < LaneBuild.Points.Num(); ++PointIndex)
        {
            const int32 PreviousPointIndex = FMath::Max(PointIndex - 1, 0);
            const int32 NextPointIndex =
                FMath::Min(PointIndex + 1, LaneBuild.Points.Num() - 1);
            const FVector Tangent =
                (LaneBuild.Points[NextPointIndex] -
                 LaneBuild.Points[PreviousPointIndex]).GetSafeNormal2D();
            const FVector Right = Tangent.IsNearlyZero()
                ? FVector::RightVector
                : FVector(-Tangent.Y, Tangent.X, 0.0f);
            Storage.BoundaryPoints.Add(
                LaneBuild.Points[PointIndex] + Right * HalfLaneWidth);
        }
        for (int32 PointIndex = LaneBuild.Points.Num() - 1; PointIndex >= 0; --PointIndex)
        {
            const int32 PreviousPointIndex = FMath::Max(PointIndex - 1, 0);
            const int32 NextPointIndex =
                FMath::Min(PointIndex + 1, LaneBuild.Points.Num() - 1);
            const FVector Tangent =
                (LaneBuild.Points[NextPointIndex] -
                 LaneBuild.Points[PreviousPointIndex]).GetSafeNormal2D();
            const FVector Right = Tangent.IsNearlyZero()
                ? FVector::RightVector
                : FVector(-Tangent.Y, Tangent.X, 0.0f);
            Storage.BoundaryPoints.Add(
                LaneBuild.Points[PointIndex] - Right * HalfLaneWidth);
        }
        Zone.BoundaryPointsEnd = Storage.BoundaryPoints.Num();

        FBox LaneBounds(ForceInit);
        for (int32 BoundaryPointIndex = Zone.BoundaryPointsBegin;
             BoundaryPointIndex < Zone.BoundaryPointsEnd;
             ++BoundaryPointIndex)
        {
            LaneBounds += Storage.BoundaryPoints[BoundaryPointIndex];
        }
        LaneBounds = LaneBounds.ExpandBy(FVector(0.0f, 0.0f, 100.0f));

        Zone.LanesBegin = LaneIndex;
        Zone.LanesEnd = LaneIndex + 1;
        Zone.Bounds = LaneBounds;
        Zone.Tags = FZoneGraphTagMask(1);
        Storage.Zones.Add(Zone);
        RouteBounds += LaneBounds;
    }
    Storage.Bounds = RouteBounds;
    FOpenMassCrowdLinearZoneBVTree LinearBVTree;
    LinearBVTree.BuildLinear(Storage.Zones);
    Storage.ZoneBVTree = MoveTemp(LinearBVTree);
    StorageLock.Unlock();

    ZoneGraphSubsystem->RegisterZoneGraphData(*RuntimeZoneGraphData);
    const FZoneGraphDataHandle DataHandle = RuntimeZoneGraphData->GetStorage().DataHandle;
    if (!DataHandle.IsValid())
    {
        return false;
    }

    RuntimeLaneHandles.Reset(Storage.Lanes.Num());
    for (int32 LaneIndex = 0; LaneIndex < Storage.Lanes.Num(); ++LaneIndex)
    {
        RuntimeLaneHandles.Emplace(LaneIndex, DataHandle);
    }

    // Fail early if the generated topology cannot exercise a genuine
    // multi-lane route. This also protects against accidentally regressing the
    // graph back to disconnected or self-looping lanes.
    FZoneGraphLaneLocation ValidationStart;
    FZoneGraphLaneLocation ValidationEnd;
    const FZoneGraphLaneHandle ValidationStartHandle = RuntimeLaneHandles[0];
    const FZoneGraphLaneHandle ValidationEndHandle = RuntimeLaneHandles[RuntimeLaneHandles.Num() - 2];
    float ValidationStartLength = 0.0f;
    float ValidationEndLength = 0.0f;
    if (!ZoneGraphSubsystem->GetLaneLength(ValidationStartHandle, ValidationStartLength) ||
        !ZoneGraphSubsystem->GetLaneLength(ValidationEndHandle, ValidationEndLength) ||
        !ZoneGraphSubsystem->CalculateLocationAlongLane(
            ValidationStartHandle,
            ValidationStartLength * 0.25f,
            ValidationStart) ||
        !ZoneGraphSubsystem->CalculateLocationAlongLane(
            ValidationEndHandle,
            ValidationEndLength * 0.75f,
            ValidationEnd))
    {
        return false;
    }

    FZoneGraphAStarWrapper ValidationGraph(Storage);
    FZoneGraphAStar ValidationPathfinder(ValidationGraph);
    const FZoneGraphAStarNode ValidationStartNode(
        ValidationStart.LaneHandle.Index,
        ValidationStart.Position);
    const FZoneGraphAStarNode ValidationEndNode(
        ValidationEnd.LaneHandle.Index,
        ValidationEnd.Position);
    const FZoneGraphPathFilter ValidationFilter(Storage, ValidationStart, ValidationEnd);
    TArray<FZoneGraphAStarWrapper::FNodeRef> ValidationPath;
    const EGraphAStarResult ValidationResult = ValidationPathfinder.FindPath(
        ValidationStartNode,
        ValidationEndNode,
        ValidationFilter,
        ValidationPath);
    if (ValidationResult != EGraphAStarResult::SearchSuccess || ValidationPath.Num() < 3)
    {
        UE_LOG(
            LogTemp,
            Error,
            TEXT("OPEN_MASS_CROWD_ASTAR_INVALID result=%d path_lanes=%d"),
            static_cast<int32>(ValidationResult),
            ValidationPath.Num());
        return false;
    }

    UE_LOG(
        LogTemp,
        Warning,
        TEXT("OPEN_MASS_CROWD_ASTAR_READY nodes=%d lanes=%d validation_path_lanes=%d lane_width=%.1f support_spacing_cm=%.1f corridor_tracks=3 cross_support_spacing_cm=%.1f agent_radius_cm=%.1f pruned_edges=%d"),
        GroundedRoute.Num(),
        RuntimeLaneHandles.Num(),
        ValidationPath.Num(),
        EffectiveLaneWidth,
        MaxPedestrianSupportSpacing,
        MaxPedestrianSupportSpacing,
        PedestrianClearanceRadius,
        RejectedEdgeCount);
    RuntimeNetworkNodeCount = GroundedRoute.Num();
    return true;
}

void AOpenMassCrowdSpawner::SpawnMassPopulation()
{
    if (!GetWorld() || !SpawnedEntities.IsEmpty())
    {
        return;
    }

    TArray<FVector> GroundedRoute;
    if (!BuildGroundedRoute(GroundedRoute))
    {
        ++GroundRetryCount;
        if (GroundRetryCount <= MaxGroundRetries)
        {
            if (GroundRetryCount == 1 || GroundRetryCount % 5 == 0)
            {
                UE_LOG(
                    LogTemp,
                    Warning,
                    TEXT("OPEN_MASS_CROWD_WAIT_ROUTE retry=%d/%d"),
                    GroundRetryCount,
                    MaxGroundRetries);
            }
            GetWorldTimerManager().SetTimer(
                SpawnRetryTimer,
                this,
                &AOpenMassCrowdSpawner::RetrySpawn,
                1.0f,
                false);
        }
        else
        {
            UE_LOG(
                LogTemp,
                Error,
                TEXT("OPEN_MASS_CROWD_ABORT Cesium route unavailable after %d retries"),
                MaxGroundRetries);
        }
        return;
    }

    if (!BuildRuntimeZoneGraph(GroundedRoute))
    {
        DestroyRuntimePopulation();
        ++GroundRetryCount;
        if (GroundRetryCount <= MaxGroundRetries)
        {
            if (GroundRetryCount == 1 || GroundRetryCount % 5 == 0)
            {
                UE_LOG(
                    LogTemp,
                    Warning,
                    TEXT("OPEN_MASS_CROWD_WAIT_NETWORK retry=%d/%d"),
                    GroundRetryCount,
                    MaxGroundRetries);
            }
            // Cesium loads collision tiles asynchronously. A centre trace can
            // already work while one of the three certified corridor tracks is
            // still absent; wait for it rather than accepting a partial lane or
            // permanently aborting this PIE session.
            GetWorldTimerManager().SetTimer(
                SpawnRetryTimer,
                this,
                &AOpenMassCrowdSpawner::RetrySpawn,
                1.0f,
                false);
        }
        else
        {
            UE_LOG(
                LogTemp,
                Error,
                TEXT("OPEN_MASS_CROWD_ABORT certified Cesium network unavailable after %d retries"),
                MaxGroundRetries);
        }
        return;
    }

    if (!SpawnEntitiesOnLanes())
    {
        UE_LOG(LogTemp, Error, TEXT("OPEN_MASS_CROWD_ABORT runtime Mass/ZoneGraph setup failed"));
        DestroyRuntimePopulation();
        return;
    }

    GroundRetryCount = 0;
    UE_LOG(
        LogTemp,
        Warning,
        TEXT("OPEN_MASS_CROWD_READY requested=%d spawned=%d lanes=%d cesium_network_nodes=%d center=(%.2f,%.2f,%.2f)"),
        PopulationCount,
        SpawnedEntities.Num(),
        RuntimeLaneHandles.Num(),
        GroundedRoute.Num(),
        GetActorLocation().X,
        GetActorLocation().Y,
        GetActorLocation().Z);
}

void AOpenMassCrowdSpawner::RetrySpawn()
{
    if (!SpawnedEntities.IsEmpty())
    {
        return;
    }

    FVector CenterGround;
    if (!ProjectToCesiumGround(GetActorLocation(), CenterGround))
    {
        ++GroundRetryCount;
        if (GroundRetryCount <= MaxGroundRetries)
        {
            if (GroundRetryCount == 1 || GroundRetryCount % 5 == 0)
            {
                UE_LOG(
                    LogTemp,
                    Warning,
                    TEXT("OPEN_MASS_CROWD_WAIT_CESIUM retry=%d/%d"),
                    GroundRetryCount,
                    MaxGroundRetries);
            }
            GetWorldTimerManager().SetTimer(
                SpawnRetryTimer,
                this,
                &AOpenMassCrowdSpawner::RetrySpawn,
                1.0f,
                false);
            return;
        }

        UE_LOG(
            LogTemp,
            Error,
            TEXT("OPEN_MASS_CROWD_ABORT Cesium ground unavailable after %d retries"),
            MaxGroundRetries);
        return;
    }

    SpawnMassPopulation();
}

bool AOpenMassCrowdSpawner::SpawnEntitiesOnLanes()
{
    UMassSpawnerSubsystem* SpawnerSubsystem =
        UWorld::GetSubsystem<UMassSpawnerSubsystem>(GetWorld());
    UZoneGraphSubsystem* ZoneGraphSubsystem =
        UWorld::GetSubsystem<UZoneGraphSubsystem>(GetWorld());
    UMassCrowdSubsystem* CrowdSubsystem =
        UWorld::GetSubsystem<UMassCrowdSubsystem>(GetWorld());
    if (!SpawnerSubsystem || !ZoneGraphSubsystem || !CrowdSubsystem || RuntimeLaneHandles.IsEmpty())
    {
        return false;
    }

    struct FResolvedVisualVariant
    {
        FName Name;
        TObjectPtr<UStaticMesh> Mesh;
        TArray<TObjectPtr<UMaterialInterface>> MaterialOverrides;
        FTransform LocalTransform = FTransform::Identity;
        FAnimToTextureAutoPlayData AutoPlayData;
        TSubclassOf<AActor> HighResTemplateActor;
        TSubclassOf<AActor> LowResTemplateActor;
        bool bUseActorRepresentation = false;
        bool bHasVAT = false;
        bool bCastShadows = true;
    };

    TArray<FResolvedVisualVariant> ResolvedVariants;
    ResolvedVariants.Reserve(VisualVariants.Num());
    for (const FOpenMassCrowdVisualConfig& VisualConfig : VisualVariants)
    {
        UStaticMesh* StaticMesh = nullptr;
        FAnimToTextureAutoPlayData AutoPlayData;
        bool bHasVAT = false;
        const bool bHasAnyVATReference =
            !VisualConfig.StaticMesh.IsNull() ||
            !VisualConfig.AnimationData.IsNull() ||
            !VisualConfig.AnimationSequence.IsNull();

        // A VAT-only variant retains the original strict validation. Actor
        // variants may intentionally omit all three VAT references.
        if (bHasAnyVATReference || !VisualConfig.bUseActorRepresentation)
        {
            StaticMesh = VisualConfig.StaticMesh.LoadSynchronous();
            UAnimToTextureDataAsset* AnimationData = VisualConfig.AnimationData.LoadSynchronous();
            UAnimSequence* AnimationSequence = VisualConfig.AnimationSequence.LoadSynchronous();
            if (!StaticMesh || !AnimationData || !AnimationSequence)
            {
                UE_LOG(
                    LogTemp,
                    Error,
                    TEXT("OPEN_MASS_CROWD_VISUAL_ASSET_INVALID variant=%s mesh=%s data=%s sequence=%s"),
                    *VisualConfig.VariantName.ToString(),
                    *GetNameSafe(StaticMesh),
                    *GetNameSafe(AnimationData),
                    *GetNameSafe(AnimationSequence));
            }
            else
            {
                const int32 AnimationIndex = AnimationData->GetIndexFromAnimSequence(AnimationSequence);
                if (AnimationIndex == INDEX_NONE ||
                    !UAnimToTextureInstancePlaybackLibrary::GetAutoPlayDataFromDataAsset(
                        AnimationData,
                        AnimationIndex,
                        AutoPlayData) ||
                    AutoPlayData.EndFrame <= AutoPlayData.StartFrame)
                {
                    UE_LOG(
                        LogTemp,
                        Error,
                        TEXT("OPEN_MASS_CROWD_VAT_ANIMATION_INVALID variant=%s sequence=%s index=%d"),
                        *VisualConfig.VariantName.ToString(),
                        *GetNameSafe(AnimationSequence),
                        AnimationIndex);
                }
                else
                {
                    bHasVAT = true;
                }
            }
        }

        TSubclassOf<AActor> HighResTemplateActor;
        TSubclassOf<AActor> LowResTemplateActor;
        if (VisualConfig.bUseActorRepresentation)
        {
            UClass* LoadedHighResClass = VisualConfig.HighResTemplateActor.LoadSynchronous();
            UClass* LoadedLowResClass = VisualConfig.LowResTemplateActor.LoadSynchronous();
            // Levels saved before the dedicated low-cost class existed have
            // the high actor serialized into both slots. Repair that legacy
            // value at runtime so clean restarts still exercise real Mass LOD.
            if (LoadedHighResClass &&
                LoadedHighResClass->IsChildOf(AOpenMassCrowdCitySampleActor::StaticClass()) &&
                (!LoadedLowResClass || LoadedLowResClass == LoadedHighResClass))
            {
                LoadedLowResClass = AOpenMassCrowdCitySampleLowResActor::StaticClass();
            }
            if (!LoadedHighResClass && !LoadedLowResClass)
            {
                UE_LOG(
                    LogTemp,
                    Error,
                    TEXT("OPEN_MASS_CROWD_ACTOR_ASSET_INVALID variant=%s high=%s low=%s"),
                    *VisualConfig.VariantName.ToString(),
                    *VisualConfig.HighResTemplateActor.ToSoftObjectPath().ToString(),
                    *VisualConfig.LowResTemplateActor.ToSoftObjectPath().ToString());
                continue;
            }

            // Either class is optional. Mirroring the available class into the
            // other slot keeps every requested Actor LOD spawnable.
            HighResTemplateActor = LoadedHighResClass ? LoadedHighResClass : LoadedLowResClass;
            LowResTemplateActor = LoadedLowResClass ? LoadedLowResClass : LoadedHighResClass;
        }

        if (!bHasVAT && !VisualConfig.bUseActorRepresentation)
        {
            continue;
        }

        FResolvedVisualVariant& Resolved = ResolvedVariants.AddDefaulted_GetRef();
        Resolved.Name = VisualConfig.VariantName;
        Resolved.Mesh = StaticMesh;
        Resolved.LocalTransform = VisualConfig.LocalTransform;
        Resolved.AutoPlayData = AutoPlayData;
        Resolved.HighResTemplateActor = HighResTemplateActor;
        Resolved.LowResTemplateActor = LowResTemplateActor;
        Resolved.bUseActorRepresentation = VisualConfig.bUseActorRepresentation;
        Resolved.bHasVAT = bHasVAT;
        Resolved.bCastShadows = VisualConfig.bCastShadows;
        if (bHasVAT)
        {
            Resolved.MaterialOverrides.Reserve(VisualConfig.MaterialOverrides.Num());
            for (const TSoftObjectPtr<UMaterialInterface>& MaterialReference : VisualConfig.MaterialOverrides)
            {
                Resolved.MaterialOverrides.Add(MaterialReference.LoadSynchronous());
            }
        }
    }

    if (ResolvedVariants.IsEmpty())
    {
        UE_LOG(LogTemp, Error, TEXT("OPEN_MASS_CROWD_NO_VALID_VISUAL_VARIANTS"));
        return false;
    }

    RuntimeTraits.Reset();
    SpawnedEntities.Reset();
    SpawnedEntities.Reserve(PopulationCount);

    for (int32 VariantIndex = 0; VariantIndex < ResolvedVariants.Num(); ++VariantIndex)
    {
        const int32 VariantPopulation =
            PopulationCount / ResolvedVariants.Num() +
            (VariantIndex < PopulationCount % ResolvedVariants.Num() ? 1 : 0);
        if (VariantPopulation == 0)
        {
            continue;
        }

        const FResolvedVisualVariant& Resolved = ResolvedVariants[VariantIndex];

        // The default constructor intentionally gives each visual variant a
        // distinct template GUID. Using the spawner as deterministic config
        // owner for every variant would incorrectly reuse the first template.
        FMassEntityConfig EntityConfig;
        EntityConfig.SetOwner(*this);
        AddRuntimeTrait<UOpenMassCrowdTrait>(*this, EntityConfig, RuntimeTraits);
        AddRuntimeTrait<UMassMovementTrait>(*this, EntityConfig, RuntimeTraits);
        AddRuntimeTrait<UMassSteeringTrait>(*this, EntityConfig, RuntimeTraits);
        AddRuntimeTrait<UMassSmoothOrientationTrait>(*this, EntityConfig, RuntimeTraits);
        AddRuntimeTrait<UMassNavigationObstacleTrait>(*this, EntityConfig, RuntimeTraits);
        AddRuntimeTrait<UMassObstacleAvoidanceTrait>(*this, EntityConfig, RuntimeTraits);
        AddRuntimeTrait<UMassZoneGraphNavigationTrait>(*this, EntityConfig, RuntimeTraits);
        AddRuntimeTrait<UMassCrowdMemberTrait>(*this, EntityConfig, RuntimeTraits);
        AddRuntimeTrait<UMassLODCollectorTrait>(*this, EntityConfig, RuntimeTraits);

        UMassCrowdVisualizationTrait* VisualizationTrait =
            AddRuntimeTrait<UMassCrowdVisualizationTrait>(
                *this,
                EntityConfig,
                RuntimeTraits);
        VisualizationTrait->HighResTemplateActor = Resolved.HighResTemplateActor;
        VisualizationTrait->LowResTemplateActor = Resolved.LowResTemplateActor;
        if (Resolved.bUseActorRepresentation)
        {
            VisualizationTrait->Params.LODRepresentation[EMassLOD::High] =
                EMassRepresentationType::HighResSpawnedActor;
            VisualizationTrait->Params.LODRepresentation[EMassLOD::Medium] =
                EMassRepresentationType::LowResSpawnedActor;
            VisualizationTrait->Params.LODRepresentation[EMassLOD::Low] =
                Resolved.bHasVAT
                    ? EMassRepresentationType::StaticMeshInstance
                    : EMassRepresentationType::LowResSpawnedActor;
        }
        else
        {
            VisualizationTrait->Params.LODRepresentation[EMassLOD::High] =
                EMassRepresentationType::StaticMeshInstance;
            VisualizationTrait->Params.LODRepresentation[EMassLOD::Medium] =
                EMassRepresentationType::StaticMeshInstance;
            VisualizationTrait->Params.LODRepresentation[EMassLOD::Low] =
                EMassRepresentationType::StaticMeshInstance;
        }
        VisualizationTrait->Params.LODRepresentation[EMassLOD::Off] =
            EMassRepresentationType::None;
        VisualizationTrait->Params.bKeepLowResActors = false;
        VisualizationTrait->LODParams.LODMaxCount[EMassLOD::High] = 500;
        VisualizationTrait->LODParams.LODMaxCount[EMassLOD::Medium] = 500;
        VisualizationTrait->LODParams.LODMaxCount[EMassLOD::Low] = 500;
        VisualizationTrait->LODParams.LODMaxCount[EMassLOD::Off] =
            TNumericLimits<int32>::Max();
        // Bounded demo ranges make the transition observable while keeping all
        // 30 pedestrians represented across the normal Hong Kong editor views.
        VisualizationTrait->LODParams.BaseLODDistance[EMassLOD::High] = 0.0f;
        VisualizationTrait->LODParams.BaseLODDistance[EMassLOD::Medium] = 1200.0f;
        VisualizationTrait->LODParams.BaseLODDistance[EMassLOD::Low] = 3500.0f;
        VisualizationTrait->LODParams.BaseLODDistance[EMassLOD::Off] = 100000.0f;
        VisualizationTrait->LODParams.VisibleLODDistance[EMassLOD::High] = 0.0f;
        VisualizationTrait->LODParams.VisibleLODDistance[EMassLOD::Medium] = 1200.0f;
        VisualizationTrait->LODParams.VisibleLODDistance[EMassLOD::Low] = 3500.0f;
        VisualizationTrait->LODParams.VisibleLODDistance[EMassLOD::Off] = 100000.0f;

        if (Resolved.bHasVAT)
        {
            FMassStaticMeshInstanceVisualizationMeshDesc MeshDesc;
            MeshDesc.Mesh = Resolved.Mesh;
            MeshDesc.MaterialOverrides = Resolved.MaterialOverrides;
            MeshDesc.LocalTransform = Resolved.LocalTransform;
            MeshDesc.bCastShadows = Resolved.bCastShadows;
            MeshDesc.Mobility = EComponentMobility::Movable;
            MeshDesc.SetSignificanceRange(EMassLOD::High, EMassLOD::Max);
            VisualizationTrait->StaticMeshInstanceDesc.Meshes.Add(MoveTemp(MeshDesc));

            UOpenMassCrowdVATPlaybackTrait* PlaybackTrait =
                AddRuntimeTrait<UOpenMassCrowdVATPlaybackTrait>(
                    *this,
                    EntityConfig,
                    RuntimeTraits);
            PlaybackTrait->StartFrame = Resolved.AutoPlayData.StartFrame;
            PlaybackTrait->EndFrame = Resolved.AutoPlayData.EndFrame;
        }

        if (!EntityConfig.ValidateEntityTemplate(*GetWorld()))
        {
            UE_LOG(
                LogTemp,
                Error,
                TEXT("OPEN_MASS_CROWD_TEMPLATE_INVALID variant=%s"),
                *Resolved.Name.ToString());
            return false;
        }

        const FMassEntityTemplate& EntityTemplate =
            EntityConfig.GetOrCreateEntityTemplate(*GetWorld());
        const int32 EntityCountBeforeSpawn = SpawnedEntities.Num();
        TSharedPtr<FMassEntityManager::FEntityCreationContext> CreationContext =
            SpawnerSubsystem->SpawnEntities(
                EntityTemplate,
                VariantPopulation,
                SpawnedEntities);
        if (!CreationContext.IsValid() ||
            SpawnedEntities.Num() - EntityCountBeforeSpawn != VariantPopulation)
        {
            return false;
        }

        // Finish Mass observers before assigning our generated lane handles.
        CreationContext.Reset();

        if (Resolved.bHasVAT)
        {
            UE_LOG(
                LogTemp,
                Log,
                TEXT("OPEN_MASS_CROWD_VAT_VARIANT name=%s count=%d frames=%.0f..%.0f"),
                *Resolved.Name.ToString(),
                VariantPopulation,
                Resolved.AutoPlayData.StartFrame,
                Resolved.AutoPlayData.EndFrame);
        }
        if (Resolved.bUseActorRepresentation)
        {
            UE_LOG(
                LogTemp,
                Log,
                TEXT("OPEN_MASS_CROWD_ACTOR_VARIANT name=%s count=%d high=%s low=%s vat_low_lod=%s"),
                *Resolved.Name.ToString(),
                VariantPopulation,
                *GetNameSafe(Resolved.HighResTemplateActor.Get()),
                *GetNameSafe(Resolved.LowResTemplateActor.Get()),
                Resolved.bHasVAT ? TEXT("true") : TEXT("false"));
        }
    }

    if (SpawnedEntities.Num() != PopulationCount)
    {
        return false;
    }

    FMassEntityManager& EntityManager = SpawnerSubsystem->GetEntityManagerChecked();
    EntityRouteStates.Reset();
    EntityRouteStates.SetNum(SpawnedEntities.Num());
    LastValidGroundStates.Reset();
    LastValidGroundStates.SetNum(SpawnedEntities.Num());
    RouteAssignmentCount = 0;
    CompletedTripCount = 0;
    RouteReplanCount = 0;
    GroundProjectionFailureCount = 0;
    GroundRollbackCount = 0;
    GroundCenterRecoveryCount = 0;
    GroundUnrecoverableCount = 0;
    CurrentUnsupportedVisualCount = 0;
    MaxConsecutiveGroundMisses = 0;
    const int32 RouteSeed =
        PopulationCount * 7919 ^
        FMath::RoundToInt(GetActorLocation().X) * 31 ^
        FMath::RoundToInt(GetActorLocation().Y) * 17;
    RouteRandomStream.Initialize(RouteSeed);

    for (int32 EntityIndex = 0; EntityIndex < SpawnedEntities.Num(); ++EntityIndex)
    {
        const FMassEntityHandle Entity = SpawnedEntities[EntityIndex];
        const FZoneGraphLaneHandle LaneHandle = RuntimeLaneHandles[EntityIndex % RuntimeLaneHandles.Num()];
        float LaneLength = 0.0f;
        if (!ZoneGraphSubsystem->GetLaneLength(LaneHandle, LaneLength) || LaneLength <= 1.0f)
        {
            return false;
        }

        // Golden-ratio phase spacing avoids stacking entities at lane starts,
        // even when the population exceeds the number of runtime lanes.
        const float SpawnPhase = FMath::Frac(
            (static_cast<float>(EntityIndex) + 1.0f) * 0.61803398875f);
        const float DistanceAlongLane = LaneLength * (0.12f + 0.68f * SpawnPhase);

        FZoneGraphLaneLocation SpawnLocation;
        if (!ZoneGraphSubsystem->CalculateLocationAlongLane(
            LaneHandle,
            DistanceAlongLane,
            SpawnLocation))
        {
            return false;
        }

        FVector SpawnGroundPoint;
        if (!ProjectToCesiumGround(SpawnLocation.Position, SpawnGroundPoint))
        {
            UE_LOG(
                LogTemp,
                Warning,
                TEXT("OPEN_MASS_CROWD_SPAWN_GROUND_REJECT entity=%d lane=%d distance=%.1f"),
                EntityIndex,
                static_cast<int32>(LaneHandle.Index),
                DistanceAlongLane);
            return false;
        }
        SpawnLocation.Position =
            SpawnGroundPoint + FVector(0.0, 0.0, LaneHeightOffset);
        FTransform InitialTransform(SpawnLocation.Tangent.ToOrientationQuat(), SpawnLocation.Position);
        EntityManager.GetFragmentDataChecked<FTransformFragment>(Entity).SetTransform(InitialTransform);
        FLastValidGroundState& InitialGroundState = LastValidGroundStates[EntityIndex];
        InitialGroundState.Transform = InitialTransform;
        InitialGroundState.LaneHandle = LaneHandle;
        InitialGroundState.DistanceAlongLane = DistanceAlongLane;
        InitialGroundState.LaneLength = LaneLength;
        InitialGroundState.bValid = true;
        EntityManager.GetFragmentDataChecked<FAgentRadiusFragment>(Entity).Radius = 30.0f;

        if (FOpenMassCrowdVATPlaybackFragment* Playback =
            EntityManager.GetFragmentDataPtr<FOpenMassCrowdVATPlaybackFragment>(Entity))
        {
            const float Phase = FMath::Frac((static_cast<float>(EntityIndex) + 1.0f) * 0.61803398875f);
            Playback->TimeOffset = Phase * VATTimeOffsetSpread;
            Playback->PlayRate = 0.9f + 0.01f * static_cast<float>((EntityIndex * 7) % 21);
        }

        FMassMoveTargetFragment& MoveTarget =
            EntityManager.GetFragmentDataChecked<FMassMoveTargetFragment>(Entity);
        MoveTarget.Center = SpawnLocation.Position;
        MoveTarget.Forward = SpawnLocation.Tangent.GetSafeNormal2D();
        MoveTarget.DistanceToGoal = 0.0f;
        MoveTarget.EntityDistanceToGoal = FMassMoveTargetFragment::UnsetDistance;
        MoveTarget.SlackRadius = 0.0f;

        FMassZoneGraphLaneLocationFragment& LaneLocation =
            EntityManager.GetFragmentDataChecked<FMassZoneGraphLaneLocationFragment>(Entity);
        LaneLocation.LaneHandle = LaneHandle;
        LaneLocation.DistanceAlongLane = DistanceAlongLane;
        LaneLocation.LaneLength = LaneLength;

        FMassCrowdLaneTrackingFragment& LaneTracking =
            EntityManager.GetFragmentDataChecked<FMassCrowdLaneTrackingFragment>(Entity);
        CrowdSubsystem->OnEntityLaneChanged(Entity, FZoneGraphLaneHandle(), LaneHandle);
        LaneTracking.TrackedLaneHandle = LaneHandle;
    }

    for (int32 EntityIndex = 0; EntityIndex < SpawnedEntities.Num(); ++EntityIndex)
    {
        if (!PlanNewDestination(EntityIndex) || !RequestNextPath(EntityIndex))
        {
            UE_LOG(
                LogTemp,
                Error,
                TEXT("OPEN_MASS_CROWD_PATH_INIT_FAILED entity=%d"),
                EntityIndex);
            return false;
        }
    }

    return true;
}

bool AOpenMassCrowdSpawner::PlanNewDestination(const int32 EntityIndex)
{
    UMassSpawnerSubsystem* SpawnerSubsystem =
        UWorld::GetSubsystem<UMassSpawnerSubsystem>(GetWorld());
    UZoneGraphSubsystem* ZoneGraphSubsystem =
        UWorld::GetSubsystem<UZoneGraphSubsystem>(GetWorld());
    if (!SpawnerSubsystem || !ZoneGraphSubsystem || !IsValid(RuntimeZoneGraphData) ||
        !SpawnedEntities.IsValidIndex(EntityIndex) ||
        !EntityRouteStates.IsValidIndex(EntityIndex) ||
        RuntimeLaneHandles.Num() < 3)
    {
        return false;
    }

    FMassEntityManager& EntityManager = SpawnerSubsystem->GetEntityManagerChecked();
    const FMassEntityHandle Entity = SpawnedEntities[EntityIndex];
    if (!EntityManager.IsEntityValid(Entity))
    {
        return false;
    }

    const FMassZoneGraphLaneLocationFragment& CurrentLane =
        EntityManager.GetFragmentDataChecked<FMassZoneGraphLaneLocationFragment>(Entity);
    FZoneGraphLaneLocation StartLocation;
    if (!ZoneGraphSubsystem->CalculateLocationAlongLane(
        CurrentLane.LaneHandle,
        CurrentLane.DistanceAlongLane,
        StartLocation))
    {
        return false;
    }

    const FZoneGraphStorage& Storage = RuntimeZoneGraphData->GetStorage();
    TArray<FZoneGraphAStarWrapper::FNodeRef> SelectedPath;
    float SelectedDestinationDistance = 0.0f;
    int32 SelectedDestinationLaneIndex = INDEX_NONE;

    // Prefer destinations at least two transitions away. Besides producing a
    // visibly meaningful trip, this guarantees every entity exercises the
    // official multi-lane A* path rather than a single-lane shortcut.
    const int32 FirstDestinationLaneIndex = RouteRandomStream.RandRange(
        0,
        RuntimeLaneHandles.Num() - 1);
    const int32 MaxDestinationAttempts = RuntimeLaneHandles.Num();
    for (int32 Attempt = 0; Attempt < MaxDestinationAttempts; ++Attempt)
    {
        // A randomized cyclic scan still visits every lane and cannot fail
        // merely through an unlucky sequence of repeated RNG values.
        const int32 DestinationLaneIndex =
            (FirstDestinationLaneIndex + Attempt) % RuntimeLaneHandles.Num();
        const FZoneGraphLaneHandle DestinationLane =
            RuntimeLaneHandles[DestinationLaneIndex];
        if (DestinationLane == CurrentLane.LaneHandle)
        {
            continue;
        }

        float DestinationLaneLength = 0.0f;
        if (!ZoneGraphSubsystem->GetLaneLength(DestinationLane, DestinationLaneLength) ||
            DestinationLaneLength <= 1.0f)
        {
            continue;
        }

        const float DestinationDistance = DestinationLaneLength *
            RouteRandomStream.FRandRange(0.35f, 0.85f);
        FZoneGraphLaneLocation EndLocation;
        if (!ZoneGraphSubsystem->CalculateLocationAlongLane(
            DestinationLane,
            DestinationDistance,
            EndLocation))
        {
            continue;
        }

        FZoneGraphAStarWrapper Graph(Storage);
        FZoneGraphAStar Pathfinder(Graph);
        const FZoneGraphAStarNode StartNode(
            StartLocation.LaneHandle.Index,
            StartLocation.Position);
        const FZoneGraphAStarNode EndNode(
            EndLocation.LaneHandle.Index,
            EndLocation.Position);
        const FZoneGraphPathFilter PathFilter(Storage, StartLocation, EndLocation);
        TArray<FZoneGraphAStarWrapper::FNodeRef> CandidatePath;
        const EGraphAStarResult Result = Pathfinder.FindPath(
            StartNode,
            EndNode,
            PathFilter,
            CandidatePath);
        if (Result == EGraphAStarResult::SearchSuccess && CandidatePath.Num() >= 3 &&
            CandidatePath[0] == static_cast<int32>(CurrentLane.LaneHandle.Index))
        {
            SelectedPath = MoveTemp(CandidatePath);
            SelectedDestinationDistance = DestinationDistance;
            SelectedDestinationLaneIndex = DestinationLaneIndex;
            break;
        }
    }

    if (SelectedPath.Num() < 3 || SelectedDestinationLaneIndex == INDEX_NONE)
    {
        UE_LOG(
            LogTemp,
            Error,
            TEXT("OPEN_MASS_CROWD_ROUTE_FAILED entity=%d current_lane=%d"),
            EntityIndex,
            static_cast<int32>(CurrentLane.LaneHandle.Index));
        return false;
    }

    FEntityRouteState& RouteState = EntityRouteStates[EntityIndex];
    RouteState.Reset();
    RouteState.LanePath.Reserve(SelectedPath.Num());
    for (const FZoneGraphAStarWrapper::FNodeRef LaneIndex : SelectedPath)
    {
        RouteState.LanePath.Emplace(LaneIndex, Storage.DataHandle);
    }
    RouteState.CurrentPathIndex = 0;
    RouteState.DestinationDistance = SelectedDestinationDistance;
    ++RouteAssignmentCount;

    UE_LOG(
        LogTemp,
        Log,
        TEXT("OPEN_MASS_CROWD_ROUTE entity=%d trip=%d start_lane=%d destination_lane=%d path_lanes=%d destination_distance=%.1f"),
        EntityIndex,
        RouteState.CompletedTrips + 1,
        static_cast<int32>(CurrentLane.LaneHandle.Index),
        SelectedDestinationLaneIndex,
        RouteState.LanePath.Num(),
        RouteState.DestinationDistance);
    return true;
}

bool AOpenMassCrowdSpawner::RequestNextPath(const int32 EntityIndex)
{
    UMassSpawnerSubsystem* SpawnerSubsystem =
        UWorld::GetSubsystem<UMassSpawnerSubsystem>(GetWorld());
    UZoneGraphSubsystem* ZoneGraphSubsystem =
        UWorld::GetSubsystem<UZoneGraphSubsystem>(GetWorld());
    if (!SpawnerSubsystem || !ZoneGraphSubsystem ||
        !SpawnedEntities.IsValidIndex(EntityIndex) ||
        !EntityRouteStates.IsValidIndex(EntityIndex))
    {
        return false;
    }

    FMassEntityManager& EntityManager = SpawnerSubsystem->GetEntityManagerChecked();
    const FMassEntityHandle Entity = SpawnedEntities[EntityIndex];
    if (!EntityManager.IsEntityValid(Entity))
    {
        return false;
    }

    FMassZoneGraphLaneLocationFragment& LaneLocation =
        EntityManager.GetFragmentDataChecked<FMassZoneGraphLaneLocationFragment>(Entity);
    FMassMoveTargetFragment& MoveTarget =
        EntityManager.GetFragmentDataChecked<FMassMoveTargetFragment>(Entity);
    FMassZoneGraphShortPathFragment& ShortPath =
        EntityManager.GetFragmentDataChecked<FMassZoneGraphShortPathFragment>(Entity);
    FMassZoneGraphCachedLaneFragment& CachedLane =
        EntityManager.GetFragmentDataChecked<FMassZoneGraphCachedLaneFragment>(Entity);
    const float Radius =
        EntityManager.GetFragmentDataChecked<FAgentRadiusFragment>(Entity).Radius;

    FEntityRouteState& RouteState = EntityRouteStates[EntityIndex];
    int32 CurrentRouteIndex = INDEX_NONE;
    const int32 SearchStart = FMath::Max(RouteState.CurrentPathIndex, 0);
    for (int32 PathIndex = SearchStart; PathIndex < RouteState.LanePath.Num(); ++PathIndex)
    {
        if (RouteState.LanePath[PathIndex] == LaneLocation.LaneHandle)
        {
            CurrentRouteIndex = PathIndex;
            break;
        }
    }

    // A lane can only disappear from the route after an external correction
    // or a stale action. Replan from the entity's actual Mass lane instead of
    // fabricating a transition that is not present in ZoneGraph.
    if (CurrentRouteIndex == INDEX_NONE)
    {
        ++RouteReplanCount;
        UE_LOG(
            LogTemp,
            Warning,
            TEXT("OPEN_MASS_CROWD_REPLAN entity=%d actual_lane=%d replans=%d"),
            EntityIndex,
            static_cast<int32>(LaneLocation.LaneHandle.Index),
            RouteReplanCount);
        if (!PlanNewDestination(EntityIndex))
        {
            return false;
        }
        CurrentRouteIndex = 0;
    }

    RouteState.CurrentPathIndex = CurrentRouteIndex;
    if (CurrentRouteIndex == RouteState.LanePath.Num() - 1 &&
        LaneLocation.DistanceAlongLane >= RouteState.DestinationDistance - 5.0f)
    {
        // Preserve the trip number while planning so its route log is correct,
        // but roll it back if a new destination cannot be created. This avoids
        // counting the same finished path again on a later retry.
        ++RouteState.CompletedTrips;
        if (!PlanNewDestination(EntityIndex))
        {
            --RouteState.CompletedTrips;
            return false;
        }
        ++CompletedTripCount;
        UE_LOG(
            LogTemp,
            Warning,
            TEXT("OPEN_MASS_CROWD_DESTINATION_REACHED entity=%d trips=%d total_completed=%d lane=%d"),
            EntityIndex,
            RouteState.CompletedTrips,
            CompletedTripCount,
            static_cast<int32>(LaneLocation.LaneHandle.Index));
        CurrentRouteIndex = 0;
    }

    const bool bHasNextLane =
        CurrentRouteIndex + 1 < RouteState.LanePath.Num();

    FZoneGraphShortPathRequest PathRequest;
    PathRequest.StartPosition =
        EntityManager.GetFragmentDataChecked<FTransformFragment>(Entity).GetTransform().GetLocation();
    PathRequest.TargetDistance = bHasNextLane
        ? LaneLocation.LaneLength
        : RouteState.DestinationDistance;
    if (bHasNextLane)
    {
        PathRequest.NextLaneHandle = RouteState.LanePath[CurrentRouteIndex + 1];
        PathRequest.NextExitLinkType = EZoneLaneLinkType::Outgoing;
    }
    PathRequest.EndOfPathIntent = EMassMovementAction::Move;
    PathRequest.bMoveReverse = false;

    MoveTarget.CreateNewAction(EMassMovementAction::Move, *GetWorld());
    const float DesiredSpeed = 120.0f + static_cast<float>((EntityIndex * 17) % 36);
    return UE::MassNavigation::ActivateActionMove(
        *GetWorld(),
        this,
        Entity,
        *ZoneGraphSubsystem,
        LaneLocation,
        PathRequest,
        Radius,
        DesiredSpeed,
        MoveTarget,
        ShortPath,
        CachedLane);
}

void AOpenMassCrowdSpawner::RefreshCompletedPaths()
{
    UMassSpawnerSubsystem* SpawnerSubsystem =
        UWorld::GetSubsystem<UMassSpawnerSubsystem>(GetWorld());
    if (!SpawnerSubsystem)
    {
        return;
    }

    FMassEntityManager& EntityManager = SpawnerSubsystem->GetEntityManagerChecked();
    for (int32 EntityIndex = 0; EntityIndex < SpawnedEntities.Num(); ++EntityIndex)
    {
        const FMassEntityHandle Entity = SpawnedEntities[EntityIndex];
        if (!EntityManager.IsEntityValid(Entity))
        {
            continue;
        }

        const FMassZoneGraphShortPathFragment& ShortPath =
            EntityManager.GetFragmentDataChecked<FMassZoneGraphShortPathFragment>(Entity);
        if (ShortPath.IsDone() && !RequestNextPath(EntityIndex))
        {
            UE_LOG(
                LogTemp,
                Error,
                TEXT("OPEN_MASS_CROWD_PATH_CONTINUE_FAILED entity=%d"),
                EntityIndex);
        }
    }
}

void AOpenMassCrowdSpawner::CorrectMassGrounding()
{
    UMassSpawnerSubsystem* SpawnerSubsystem =
        UWorld::GetSubsystem<UMassSpawnerSubsystem>(GetWorld());
    if (!SpawnerSubsystem)
    {
        return;
    }

    UZoneGraphSubsystem* ZoneGraphSubsystem =
        UWorld::GetSubsystem<UZoneGraphSubsystem>(GetWorld());
    UMassCrowdSubsystem* CrowdSubsystem =
        UWorld::GetSubsystem<UMassCrowdSubsystem>(GetWorld());
    if (!ZoneGraphSubsystem || !CrowdSubsystem)
    {
        return;
    }

    FMassEntityManager& EntityManager = SpawnerSubsystem->GetEntityManagerChecked();
    int32 CorrectedCount = 0;
    CurrentUnsupportedVisualCount = 0;
    TArray<int32> PathsToRebuild;
    for (int32 EntityIndex = 0; EntityIndex < SpawnedEntities.Num(); ++EntityIndex)
    {
        const FMassEntityHandle Entity = SpawnedEntities[EntityIndex];
        if (!EntityManager.IsEntityValid(Entity))
        {
            continue;
        }

        FTransformFragment& TransformFragment =
            EntityManager.GetFragmentDataChecked<FTransformFragment>(Entity);
        FTransform& Transform = TransformFragment.GetMutableTransform();
        FMassZoneGraphLaneLocationFragment& LaneLocation =
            EntityManager.GetFragmentDataChecked<FMassZoneGraphLaneLocationFragment>(Entity);
        FMassCrowdLaneTrackingFragment& LaneTracking =
            EntityManager.GetFragmentDataChecked<FMassCrowdLaneTrackingFragment>(Entity);
        FLastValidGroundState* LastValid =
            LastValidGroundStates.IsValidIndex(EntityIndex)
                ? &LastValidGroundStates[EntityIndex]
                : nullptr;
        const FVector CurrentLocation = Transform.GetLocation();
        FVector GroundPoint;
        if (ProjectToCesiumGround(CurrentLocation, GroundPoint))
        {
            const FVector ValidLocation =
                GroundPoint + FVector(0.0, 0.0, LaneHeightOffset);
            Transform.SetLocation(ValidLocation);
            if (LastValid)
            {
                LastValid->Transform = Transform;
                LastValid->LaneHandle = LaneLocation.LaneHandle;
                LastValid->DistanceAlongLane = LaneLocation.DistanceAlongLane;
                LastValid->LaneLength = LaneLocation.LaneLength;
                LastValid->ConsecutiveMisses = 0;
                LastValid->bValid = true;
            }
            ++CorrectedCount;
            continue;
        }

        ++GroundProjectionFailureCount;
        if (LastValid)
        {
            ++LastValid->ConsecutiveMisses;
            MaxConsecutiveGroundMisses = FMath::Max(
                MaxConsecutiveGroundMisses,
                LastValid->ConsecutiveMisses);
        }

        // Avoidance may be the only reason the tentative XY left the certified
        // corridor. First recover at the exact center of the entity's current
        // lane and current progress, then rebuild its short path from there.
        FZoneGraphLaneLocation CenterLaneLocation;
        FVector CenterGroundPoint;
        if (ZoneGraphSubsystem->CalculateLocationAlongLane(
                LaneLocation.LaneHandle,
                LaneLocation.DistanceAlongLane,
                CenterLaneLocation) &&
            ProjectToCesiumGround(CenterLaneLocation.Position, CenterGroundPoint))
        {
            Transform.SetRotation(CenterLaneLocation.Tangent.ToOrientationQuat());
            Transform.SetLocation(
                CenterGroundPoint + FVector(0.0, 0.0, LaneHeightOffset));
            if (FMassVelocityFragment* Velocity =
                EntityManager.GetFragmentDataPtr<FMassVelocityFragment>(Entity))
            {
                Velocity->Value = FVector::ZeroVector;
            }
            if (LastValid)
            {
                LastValid->Transform = Transform;
                LastValid->LaneHandle = LaneLocation.LaneHandle;
                LastValid->DistanceAlongLane = LaneLocation.DistanceAlongLane;
                LastValid->LaneLength = LaneLocation.LaneLength;
                LastValid->ConsecutiveMisses = 0;
                LastValid->bValid = true;
            }
            ++GroundCenterRecoveryCount;
            ++CorrectedCount;
            PathsToRebuild.Add(EntityIndex);
            continue;
        }

        if (LastValid && LastValid->bValid)
        {
            // Collision is still missing at the certified lane center. Restore
            // both transform and ZoneGraph progress; restoring only Transform
            // would let lane state advance invisibly past the rejected step.
            Transform = LastValid->Transform;
            const FZoneGraphLaneHandle PreviousTrackedLane =
                LaneTracking.TrackedLaneHandle;
            LaneLocation.LaneHandle = LastValid->LaneHandle;
            LaneLocation.DistanceAlongLane = LastValid->DistanceAlongLane;
            LaneLocation.LaneLength = LastValid->LaneLength;
            if (PreviousTrackedLane != LastValid->LaneHandle)
            {
                CrowdSubsystem->OnEntityLaneChanged(
                    Entity,
                    PreviousTrackedLane,
                    LastValid->LaneHandle);
                LaneTracking.TrackedLaneHandle = LastValid->LaneHandle;
            }
            if (FMassVelocityFragment* Velocity =
                EntityManager.GetFragmentDataPtr<FMassVelocityFragment>(Entity))
            {
                Velocity->Value = FVector::ZeroVector;
            }
            ++GroundRollbackCount;
            PathsToRebuild.Add(EntityIndex);
            continue;
        }

        ++GroundUnrecoverableCount;
        ++CurrentUnsupportedVisualCount;
    }

    for (const int32 EntityIndex : PathsToRebuild)
    {
        if (!RequestNextPath(EntityIndex))
        {
            UE_LOG(
                LogTemp,
                Error,
                TEXT("OPEN_MASS_CROWD_GROUND_PATH_RECOVERY_FAILED entity=%d"),
                EntityIndex);
        }
    }

    if (CorrectedCount != SpawnedEntities.Num())
    {
        UE_LOG(
            LogTemp,
            Verbose,
            TEXT("OPEN_MASS_CROWD_GROUND corrected=%d total=%d"),
            CorrectedCount,
            SpawnedEntities.Num());
    }
}

void AOpenMassCrowdSpawner::SyncVisualActorsToMass()
{
    const int32 PreviousHighResCount = CurrentHighResRepresentationCount;
    const int32 PreviousLowResCount = CurrentLowResRepresentationCount;
    CurrentHighResRepresentationCount = 0;
    CurrentLowResRepresentationCount = 0;

    UMassSpawnerSubsystem* SpawnerSubsystem =
        UWorld::GetSubsystem<UMassSpawnerSubsystem>(GetWorld());
    if (!SpawnerSubsystem)
    {
        return;
    }

    FMassEntityManager& EntityManager = SpawnerSubsystem->GetEntityManagerChecked();
    static const FName LowRepresentationTier(TEXT("Low"));
    for (int32 EntityIndex = 0; EntityIndex < SpawnedEntities.Num(); ++EntityIndex)
    {
        const FMassEntityHandle Entity = SpawnedEntities[EntityIndex];
        if (!EntityManager.IsEntityValid(Entity))
        {
            continue;
        }

        const FTransformFragment* TransformFragment =
            EntityManager.GetFragmentDataPtr<FTransformFragment>(Entity);
        const FMassVelocityFragment* VelocityFragment =
            EntityManager.GetFragmentDataPtr<FMassVelocityFragment>(Entity);
        FMassActorFragment* ActorFragment =
            EntityManager.GetFragmentDataPtr<FMassActorFragment>(Entity);
        AActor* VisualActor =
            ActorFragment ? ActorFragment->GetOwnedByMassMutable() : nullptr;
        if (!TransformFragment || !IsValid(VisualActor))
        {
            continue;
        }

        if (AOpenMassCrowdCitySampleActor* CitySampleActor =
            Cast<AOpenMassCrowdCitySampleActor>(VisualActor))
        {
            const int32 AppearanceSeed =
                Entity.Index * 196613 + Entity.SerialNumber * 314159;
            CitySampleActor->SetMassAppearanceSeed(AppearanceSeed);
            CitySampleActor->SetMassSpeedCmPerSecond(
                VelocityFragment ? VelocityFragment->Value.Size2D() : 0.0f);
            if (CitySampleActor->GetRepresentationTier() == LowRepresentationTier)
            {
                ++CurrentLowResRepresentationCount;
            }
            else
            {
                ++CurrentHighResRepresentationCount;
            }
        }

        FTransform CertifiedVisualTransform = TransformFragment->GetTransform();
        const FLastValidGroundState* LastValid =
            LastValidGroundStates.IsValidIndex(EntityIndex)
                ? &LastValidGroundStates[EntityIndex]
                : nullptr;
        if (LastValid && LastValid->bValid)
        {
            // Rendering only the most recently exact-XY-certified location
            // prevents the 20 Hz collision check from exposing a speculative
            // unsupported Mass step between correction ticks.
            CertifiedVisualTransform.SetLocation(
                LastValid->Transform.GetLocation());
            VisualActor->SetActorHiddenInGame(false);
        }
        else
        {
            // An entity without a certified point must never be rendered at a
            // speculative Mass transform.
            VisualActor->SetActorHiddenInGame(true);
            continue;
        }
        if (!VisualActor->GetActorTransform().Equals(CertifiedVisualTransform, 0.01f))
        {
            VisualActor->SetActorTransform(
                CertifiedVisualTransform,
                false,
                nullptr,
                ETeleportType::TeleportPhysics);
        }
    }

    if ((CurrentHighResRepresentationCount != PreviousHighResCount ||
         CurrentLowResRepresentationCount != PreviousLowResCount) &&
        CurrentHighResRepresentationCount + CurrentLowResRepresentationCount > 0)
    {
        UE_LOG(
            LogTemp,
            Log,
            TEXT("OPEN_MASS_CROWD_REPRESENTATIONS high=%d low=%d spawned=%d"),
            CurrentHighResRepresentationCount,
            CurrentLowResRepresentationCount,
            SpawnedEntities.Num());
    }
}

void AOpenMassCrowdSpawner::DestroyRuntimePopulation()
{
    if (UMassSpawnerSubsystem* SpawnerSubsystem =
        UWorld::GetSubsystem<UMassSpawnerSubsystem>(GetWorld()))
    {
        if (!SpawnedEntities.IsEmpty())
        {
            SpawnerSubsystem->DestroyEntities(SpawnedEntities);
        }
    }
    SpawnedEntities.Reset();
    LastValidGroundStates.Reset();

    if (IsValid(RuntimeZoneGraphData))
    {
        RuntimeZoneGraphData->Destroy();
    }
    RuntimeZoneGraphData = nullptr;
    RuntimeLaneHandles.Reset();
    EntityRouteStates.Reset();
    RuntimeTraits.Reset();
    RuntimeNetworkNodeCount = 0;
    RouteAssignmentCount = 0;
    CompletedTripCount = 0;
    RouteReplanCount = 0;
    GroundProjectionFailureCount = 0;
    GroundRollbackCount = 0;
    GroundCenterRecoveryCount = 0;
    GroundUnrecoverableCount = 0;
    CurrentUnsupportedVisualCount = 0;
    MaxConsecutiveGroundMisses = 0;
    CurrentHighResRepresentationCount = 0;
    CurrentLowResRepresentationCount = 0;
}
