#include "OpenMassCrowdSpawner.h"

#include "OpenMassCrowdCitySampleActor.h"
#include "OpenMassCrowdTrait.h"
#include "OpenMassCrowdVisualization.h"

#include "Algo/Sort.h"
#include "Animation/AnimSequence.h"
#include "AnimToTextureDataAsset.h"
#include "AnimToTextureInstancePlaybackHelpers.h"
#include "Avoidance/MassAvoidanceTrait.h"
#include "Avoidance/MassNavigationObstacleTrait.h"
#include "Components/LineBatchComponent.h"
#include "Components/PrimitiveComponent.h"
#include "Components/SceneComponent.h"
#include "Components/StaticMeshComponent.h"
#include "Camera/PlayerCameraManager.h"
#include "DrawDebugHelpers.h"
#include "Engine/Engine.h"
#include "Engine/GameViewportClient.h"
#include "Engine/LocalPlayer.h"
#include "Engine/World.h"
#include "Engine/StaticMesh.h"
#include "EngineUtils.h"
#include "GameFramework/Pawn.h"
#include "GameFramework/PlayerController.h"
#include "InputCoreTypes.h"
#include "Kismet/GameplayStatics.h"
#include "MassActorSubsystem.h"
#include "MassCommonFragments.h"
#include "MassCrowdFragments.h"
#include "MassCrowdMemberTrait.h"
#include "MassCrowdSubsystem.h"
#include "MassCrowdVisualizationTrait.h"
#include "MassEntityConfigAsset.h"
#include "MassEntityManager.h"
#include "MassRepresentationFragments.h"
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
#include "Misc/App.h"
#include "Misc/ScopeLock.h"
#include "SmoothOrientation/MassSmoothOrientationTrait.h"
#include "Steering/MassSteeringTrait.h"
#include "Styling/CoreStyle.h"
#include "TimerManager.h"
#include "UObject/UObjectIterator.h"
#include "Widgets/Input/SButton.h"
#include "Widgets/Layout/SBorder.h"
#include "Widgets/Layout/SBox.h"
#include "Widgets/Layout/SConstraintCanvas.h"
#include "Widgets/Layout/SSeparator.h"
#include "Widgets/SBoxPanel.h"
#include "Widgets/SOverlay.h"
#include "Widgets/SWeakWidget.h"
#include "Widgets/Text/STextBlock.h"
#include "ZoneGraphAStar.h"
#include "ZoneGraphData.h"
#include "ZoneGraphQuery.h"
#include "ZoneGraphSubsystem.h"

namespace
{
constexpr int32 MaxGroundRetries = 60;
constexpr float LaneHeightOffset = 2.0f;
constexpr float MaxPedestrianSampleHeightDelta = 55.0f;
// Photogrammetry triangle normals are noisy even on continuous pavement. The
// certifier therefore uses the independently sampled 10 cm height grade below
// as the decisive slope test and keeps a 60-degree raw-normal guard to reject
// walls. Runtime admission/guards must use the same policy as the immutable
// cache or a cache that passed exact-XY certification can be rejected in PIE.
constexpr float MinWalkableSurfaceNormalZ = 0.5f;
constexpr float MaxPedestrianSampleGrade = 0.96f;
// A 10 cm support interval is smaller than an adult footprint. A shorter gap
// can be stepped over physically; a larger unsupported gap necessarily fails
// an exact Cesium collision probe instead of becoming a floating lane chord.
constexpr float MaxPedestrianSupportSpacing = 10.0f;
constexpr float PedestrianClearanceRadius = 30.0f;
constexpr float PedestrianClearanceHalfHeight = 82.0f;
constexpr float PedestrianClearanceAboveGround = 14.0f;
// Central uses a 54 cm adult body diameter plus a 1 cm reservation gap.  The
// local proof remains on its established 30 cm radius and cache certification
// remains conservative at the same 30 cm clearance radius.
constexpr float CentralPedestrianRadius = 27.0f;
constexpr float CentralPedestrianSafetyGapCm = 1.0f;
constexpr float CentralMinimumCenterClearanceCm =
    CentralPedestrianRadius * 2.0f + CentralPedestrianSafetyGapCm;
// Certified samples are stored as floats after Cesium/world transforms. Permit
// one millimetre of representation error at the 55 cm boundary; this is not a
// smaller body model and all telemetry continues to report the measured value.
constexpr float CentralClearanceComparisonToleranceCm = 0.1f;
constexpr float CentralMinimumAcceptedCenterClearanceCm =
    CentralMinimumCenterClearanceCm - CentralClearanceComparisonToleranceCm;
// The certified cache stores discrete exact-XY Cesium samples. After removing
// sub-six-metre circulation fragments, its densest valid 100-person packing is
// on an approximately 50 cm sample lattice. Cesium/world float transforms can
// contract that measured XY separation by a few millimetres, so admit the
// exact non-overlapping lattice with a 1 cm transform margin at startup;
// normal same-direction headway immediately remains the stricter 55 cm
// body-quality spacing above.
constexpr float CentralInitialPackingClearanceCm = 49.0f;
constexpr float CentralMinimumAcceptedInitialPackingClearanceCm =
    CentralInitialPackingClearanceCm -
    CentralClearanceComparisonToleranceCm;
constexpr int32 RequiredCentralSpawnDistrictCount = 6;
constexpr int32 FullCentralPopulation = 100;
constexpr int32 CentralLaneHistoryLimit = 8;
constexpr int32 CentralDestinationHistoryLimit = 4;
// The delivery demo needs bounded, indefinitely live motion more than
// destination variety. Certified edge circulation keeps each pedestrian on a
// real forward/reverse road pair, preserves long visible walks, and removes
// the multi-junction reservation cycles that can accumulate in an unattended
// demo. The richer component-aware planner remains compiled for engineering
// modes and future simulation work.
constexpr bool bUseCentralCertifiedEdgeCirculation = true;
constexpr int32 CentralMaximumPedestriansPerEdgeCirculation = 6;
// Semantic recovery splits some certified pavement into 4-5 m fragments.
// Reversing on those tiny pairs causes Mass to complete and rebuild actions
// faster than avoidance can drain the endpoint. Keep investor circulation on
// certified lanes long enough to sustain a visible walk before turnaround.
constexpr float CentralMinimumEdgeCirculationLaneLengthCm = 600.0f;
constexpr float CentralPreferredOutboundMinimumCm = 6000.0f;
constexpr float CentralPreferredOutboundMaximumCm = 15000.0f;
constexpr float CentralRouteEndpointInsetCm = 1.0f;
constexpr float CentralPreferredSpawnComponentDirectionalMinimumCm = 9000.0f;
constexpr float CentralCesiumComponentGridSizeCm = 5000.0f;
constexpr double CentralCesiumComponentCacheRefreshSeconds = 1.0;
constexpr int64 CentralCesiumMaximumBucketsPerComponent = 256;
constexpr int32 CentralGroundGuardsPerTick = 24;
// One admission pass may validate a full configured 17-person district plus a
// small deterministic reserve search, but can never fan out across thousands
// of certified samples in one frame.
constexpr int32 CentralAdmissionLiveProbeBudgetPerPass = 64;
constexpr int32 CentralMaximumReserveSlotsPerPlanIndex = 1024;
constexpr int32 CentralCellRecoveryGuardSuccessThreshold = 2;
constexpr float CentralTelemetrySampleIntervalSeconds = 1.0f;
constexpr float CentralMovingSpeedThresholdCmPerSecond = 10.0f;
// A one-second displacement sample can coincide exactly with a certified lane
// hand-off. Treat an entity that moved within the trailing two seconds as part
// of the active walking population, while the independent five-second stuck
// counter continues to fail genuine stalls.
constexpr float CentralMovementLivenessWindowSeconds = 2.0f;
// Edge circulation has no routing ambiguity. One missed one-second health
// sample is enough to renew its exact short-path action before a visual pause
// can grow into a five-second stall.
constexpr float CentralEdgePathRefreshThresholdSeconds = 0.5f;
constexpr float CentralStuckThresholdSeconds = 5.0f;
// Same-direction runtime headway keeps the 55 cm body-quality spacing after
// the separately bounded initial packing transaction.
// The hard collision contract for severe, crossing, merging, and opposing
// geometry is the independent 20 cm center-distance threshold from the gate.
constexpr float CentralSevereOverlapDistanceCm = 20.0f;
// Opposing directions within one pedestrian width share spawn admission and
// occupancy capacity. This classification is deliberately wider than the
// 20 cm runtime hard-conflict gate; movement arbitration uses only local
// sub-threshold sample resources and never serializes this whole relation.
constexpr float CentralOpposingCorridorToleranceCm =
    CentralMinimumCenterClearanceCm;
constexpr float CentralConflictWaitReplanSeconds = 1.5f;
constexpr double CentralFrameTimeRetentionWindowSeconds = 60.0;
// 20,000 samples retain a full minute even above 300 fps while placing a hard
// upper bound on evidence memory if frame pacing is accidentally disabled.
constexpr int32 CentralMaximumFrameTimeSamples = 20000;
constexpr int32 CentralFrameTimeCompactionThreshold = 512;
constexpr int32 InvestorPresentationBandCount = 7;
constexpr float InvestorPresentationBandSpacingCm = 45.0f;
constexpr int32 InvestorPresentationPhaseCount = 5;
constexpr float InvestorPresentationPhaseSpacingCm = 60.0f;
constexpr int32 InvestorGroundGuardsPerPass = 4;
constexpr float InvestorNetworkRefreshSeconds = 0.33f;
constexpr float InvestorValidatedRoofRefreshSeconds = 2.0f;
constexpr float InvestorAssociationVisualRefreshSeconds = 0.1f;
constexpr float InvestorAssociationRoofOffsetCm = 4.0f;
constexpr float InvestorAssociationDashLengthCm = 520.0f;
constexpr float InvestorAssociationDashGapCm = 300.0f;
constexpr float InvestorSkeletalWalkDistanceCm = 35000.0f;
constexpr float InvestorVATVisibleDistanceCm = 300000.0f;
constexpr int32 InvestorHighActorBudget = 6;
constexpr int32 InvestorLowActorBudget = 48;
constexpr int32 InvestorExpectedSignalActorCount = 1950;
constexpr int32 InvestorExpectedSignalSourceCount = 30;
constexpr int32 InvestorExpectedSignalRayCount = 1920;
// World Partition streams the obsolete mock signal actors after BeginPlay.
// A short bounded scan prevents those late arrivals from flashing back on top
// of the collision-certified rooftop network.
constexpr float InvestorLegacySignalSuppressionSeconds = 0.25f;

FString GetSignalActorLabel(const AActor* Actor)
{
    if (!IsValid(Actor))
    {
        return FString();
    }
#if WITH_EDITOR
    return Actor->GetActorLabel();
#else
    return Actor->GetName();
#endif
}

bool IsPersistedRooftopSignalActorLabel(const FString& Label)
{
    const bool bSource =
        Label.StartsWith(TEXT("SIG_Source_")) &&
        Label.EndsWith(TEXT("_Direct_Roof"));
    const bool bRay =
        Label.StartsWith(TEXT("SIG_Ray_")) &&
        (Label.Contains(TEXT("_Segment_")) ||
         Label.Contains(TEXT("_RoofHit_")));
    return bSource || bRay;
}

bool IsLegacyFloatingSignalActorLabel(const FString& Label)
{
    if (IsPersistedRooftopSignalActorLabel(Label))
    {
        return false;
    }
    return
        Label.StartsWith(TEXT("SIG_RaySegment_")) ||
        Label.StartsWith(TEXT("SIG_Node_")) ||
        Label.StartsWith(TEXT("SIG_Ray_HISM_")) ||
        Label.StartsWith(TEXT("SIG_Source_"));
}

bool HideLegacyFloatingSignalActor(AActor* Actor)
{
    if (!IsValid(Actor))
    {
        return false;
    }

    bool bHadVisiblePrimitive = false;
    TArray<UPrimitiveComponent*> PrimitiveComponents;
    Actor->GetComponents<UPrimitiveComponent>(PrimitiveComponents);
    for (UPrimitiveComponent* Component : PrimitiveComponents)
    {
        if (!IsValid(Component))
        {
            continue;
        }
        bHadVisiblePrimitive = bHadVisiblePrimitive ||
            (Component->IsVisible() && !Component->bHiddenInGame);
        Component->SetVisibility(false, true);
        Component->SetHiddenInGame(true, true);
        Component->MarkRenderStateDirty();
    }
    const bool bWasVisible = !Actor->IsHidden() && bHadVisiblePrimitive;
    Actor->SetActorHiddenInGame(true);
    return bWasVisible;
}

bool IsLegacyFloatingSignalActorVisible(const AActor* Actor)
{
    if (!IsValid(Actor) || Actor->IsHidden())
    {
        return false;
    }
    TArray<UPrimitiveComponent*> PrimitiveComponents;
    Actor->GetComponents<UPrimitiveComponent>(PrimitiveComponents);
    for (const UPrimitiveComponent* Component : PrimitiveComponents)
    {
        if (IsValid(Component) &&
            Component->IsVisible() && !Component->bHiddenInGame)
        {
            return true;
        }
    }
    return false;
}

int32 GetInvestorPresentationBandIndex(const int32 StableEntityIndex)
{
    const int32 SafeIndex = FMath::Max(StableEntityIndex, 0);
    // Three is coprime to seven, so neighbours do not form a left-to-right
    // staircase while every seven stable identities still occupy every band.
    return (SafeIndex * 3) % InvestorPresentationBandCount;
}

float GetInvestorPresentationOffsetCm(const int32 StableEntityIndex)
{
    return static_cast<float>(
        GetInvestorPresentationBandIndex(StableEntityIndex) -
        InvestorPresentationBandCount / 2) *
        InvestorPresentationBandSpacingCm;
}

float GetInvestorPresentationPhaseOffsetCm(const int32 StableEntityIndex)
{
    const int32 SafeIndex = FMath::Max(StableEntityIndex, 0);
    return static_cast<float>(
        (SafeIndex * 2) % InvestorPresentationPhaseCount -
        InvestorPresentationPhaseCount / 2) *
        InvestorPresentationPhaseSpacingCm;
}

FVector GetInvestorPresentationPosition(
    const FZoneGraphLaneLocation& CertifiedLocation,
    const int32 StableEntityIndex)
{
    const FVector Tangent =
        CertifiedLocation.Tangent.GetSafeNormal2D();
    const FVector Side(-Tangent.Y, Tangent.X, 0.0f);
    return CertifiedLocation.Position +
        Side * GetInvestorPresentationOffsetCm(StableEntityIndex);
}

FString GetCentralPersonId(const int32 StableEntityIndex)
{
    return FString::Printf(TEXT("HK-C-%03d"), StableEntityIndex + 1);
}

FString GetCentralPersonName(const int32 StableEntityIndex)
{
    static const TCHAR* Surnames[] = {
        TEXT("陈"), TEXT("李"), TEXT("张"), TEXT("黄"), TEXT("梁"),
        TEXT("王"), TEXT("吴"), TEXT("刘"), TEXT("林"), TEXT("杨"),
        TEXT("何"), TEXT("郑"), TEXT("罗"), TEXT("谢"), TEXT("郭"),
        TEXT("邓"), TEXT("冯"), TEXT("曾"), TEXT("萧"), TEXT("许"),
        TEXT("周"), TEXT("叶"), TEXT("苏"), TEXT("马"), TEXT("谭"),
        TEXT("潘"), TEXT("钟"), TEXT("卢"), TEXT("蔡"), TEXT("杜")};
    static const TCHAR* GivenNames[] = {
        TEXT("嘉怡"), TEXT("俊杰"), TEXT("思颖"), TEXT("子轩"), TEXT("咏晴"),
        TEXT("浩然"), TEXT("芷晴"), TEXT("文轩"), TEXT("凯琳"), TEXT("乐天")};
    const int32 SafeIndex = FMath::Max(StableEntityIndex, 0);
    return FString(Surnames[(SafeIndex / UE_ARRAY_COUNT(GivenNames)) %
        UE_ARRAY_COUNT(Surnames)]) +
        GivenNames[SafeIndex % UE_ARRAY_COUNT(GivenNames)];
}

FString GetCentralPersonOccupation(const int32 StableEntityIndex)
{
    static const TCHAR* Occupations[] = {
        TEXT("数字孪生研究员"), TEXT("城市规划师"), TEXT("通信工程师"),
        TEXT("GIS 工程师"), TEXT("建筑师"), TEXT("交通分析师"),
        TEXT("软件工程师"), TEXT("数据科学家"), TEXT("产品设计师"),
        TEXT("测绘工程师"), TEXT("可视化设计师"), TEXT("网络运维工程师"),
        TEXT("环境顾问"), TEXT("高校研究助理"), TEXT("项目经理"),
        TEXT("金融科技分析师"), TEXT("公共空间设计师"), TEXT("游戏开发者"),
        TEXT("BIM 工程师"), TEXT("媒体制作人")};
    return Occupations[FMath::Max(StableEntityIndex, 0) %
        UE_ARRAY_COUNT(Occupations)];
}

FString GetCentralPersonSoftware(const int32 StableEntityIndex)
{
    static const TCHAR* Software[] = {
        TEXT("Unreal Engine 5"), TEXT("ArcGIS Pro"), TEXT("QGIS"),
        TEXT("Python"), TEXT("Blender"), TEXT("MATLAB"), TEXT("VS Code"),
        TEXT("Figma"), TEXT("Rhino"), TEXT("AutoCAD"), TEXT("Revit"),
        TEXT("SketchUp"), TEXT("Obsidian"), TEXT("Notion"), TEXT("Tableau"),
        TEXT("Power BI"), TEXT("Docker"), TEXT("Git"), TEXT("Cesium"),
        TEXT("DaVinci Resolve")};
    return Software[(FMath::Max(StableEntityIndex, 0) * 7 + 3) %
        UE_ARRAY_COUNT(Software)];
}

FString GetCentralPersonGender(const int32 StableEntityIndex)
{
    return (FMath::Max(StableEntityIndex, 0) % 2) == 0
        ? TEXT("女  ♀")
        : TEXT("男  ♂");
}

int32 GetCentralPersonAge(const int32 StableEntityIndex)
{
    return 22 + (FMath::Max(StableEntityIndex, 0) * 11 + 7) % 39;
}

TSharedRef<SWidget> MakeCentralProfileRow(
    const FString& Label,
    const FString& Value,
    const FLinearColor& Accent)
{
    return SNew(SBorder)
        .BorderImage(FCoreStyle::Get().GetBrush("WhiteBrush"))
        .BorderBackgroundColor(FLinearColor(0.035f, 0.065f, 0.09f, 0.72f))
        .Padding(FMargin(11.0f, 7.0f))
        [
            SNew(SHorizontalBox)
            + SHorizontalBox::Slot()
            .AutoWidth()
            .VAlign(VAlign_Center)
            [
                SNew(SBox)
                .WidthOverride(3.0f)
                .HeightOverride(28.0f)
                [
                    SNew(SBorder)
                    .BorderImage(FCoreStyle::Get().GetBrush("WhiteBrush"))
                    .BorderBackgroundColor(Accent)
                ]
            ]
            + SHorizontalBox::Slot()
            .FillWidth(1.0f)
            .Padding(9.0f, 0.0f, 0.0f, 0.0f)
            [
                SNew(SVerticalBox)
                + SVerticalBox::Slot()
                .AutoHeight()
                [
                    SNew(STextBlock)
                    .Text(FText::FromString(Label.ToUpper()))
                    .Font(FCoreStyle::GetDefaultFontStyle("Regular", 8))
                    .ColorAndOpacity(FLinearColor(0.45f, 0.65f, 0.72f, 1.0f))
                ]
                + SVerticalBox::Slot()
                .AutoHeight()
                .Padding(0.0f, 1.0f, 0.0f, 0.0f)
                [
                    SNew(STextBlock)
                    .Text(FText::FromString(Value))
                    .Font(FCoreStyle::GetDefaultFontStyle("Bold", 13))
                    .ColorAndOpacity(FLinearColor(0.92f, 0.98f, 1.0f, 1.0f))
                ]
            ]
        ];
}
// MassZoneGraphNavigation caches at most five lane points and encodes lane
// distances at 10 cm precision.  Central intentionally retains every
// collision-certified ground sample, so a long request can extend beyond the
// cached geometry.  Keep a quantization allowance when proving that every
// generated short-path point is backed by the current cache window.
constexpr float CentralShortPathCacheToleranceCm = 10.01f;

bool ClampCentralShortPathToCachedCoverage(
    const bool bMoveReverse,
    const FMassZoneGraphCachedLaneFragment& CachedLane,
    FMassMoveTargetFragment& MoveTarget,
    FMassZoneGraphShortPathFragment& ShortPath,
    bool& bOutClamped)
{
    bOutClamped = false;
    if (CachedLane.NumPoints < 2 || ShortPath.NumPoints < 2)
    {
        return false;
    }

    const float CachedStart = CachedLane.LanePointProgressions[0].Get();
    const float CachedEnd =
        CachedLane.LanePointProgressions[CachedLane.NumPoints - 1].Get();
    FMassZoneGraphPathPoint& LastPoint =
        ShortPath.Points[ShortPath.NumPoints - 1];
    const float LastDistanceAlongLane = LastPoint.DistanceAlongLane.Get();
    const bool bOutsideCachedGeometry = bMoveReverse
        ? LastDistanceAlongLane <
            CachedStart - CentralShortPathCacheToleranceCm
        : LastDistanceAlongLane >
            CachedEnd + CentralShortPathCacheToleranceCm;

    if (bOutsideCachedGeometry)
    {
        // UE 5.7 clamps LastPoint.Position to the last cached segment, but
        // leaves DistanceAlongLane at the remote request target.  Rebind that
        // point to the geometry which actually produced it and mark the action
        // partial. RefreshCompletedPaths() will request the next certified
        // chunk; a next-lane transition is attached only once the real lane
        // endpoint is inside the cache.
        const float CachedBoundary = bMoveReverse ? CachedStart : CachedEnd;
        LastPoint.DistanceAlongLane = FMassInt16Real10(CachedBoundary);
        LastPoint.bIsLaneExtrema = false;
        ShortPath.NextLaneHandle.Reset();
        ShortPath.NextExitLinkType = EZoneLaneLinkType::None;
        ShortPath.bPartialResult = true;
        MoveTarget.DistanceToGoal = LastPoint.Distance.Get();
        bOutClamped = true;
    }

    // Fail closed if a future engine change produces any other on-lane point
    // whose distance is not represented by this exact cached geometry.
    for (uint8 PointIndex = 0; PointIndex < ShortPath.NumPoints; ++PointIndex)
    {
        const FMassZoneGraphPathPoint& Point = ShortPath.Points[PointIndex];
        if (Point.bOffLane)
        {
            continue;
        }

        const float PointDistanceAlongLane = Point.DistanceAlongLane.Get();
        if (PointDistanceAlongLane <
                CachedStart - CentralShortPathCacheToleranceCm ||
            PointDistanceAlongLane >
                CachedEnd + CentralShortPathCacheToleranceCm)
        {
            return false;
        }
    }

    return true;
}

FIntPoint GetCesiumGroundGridCoordinate(const FVector& Position)
{
    return FIntPoint(
        FMath::FloorToInt(Position.X / CentralCesiumComponentGridSizeCm),
        FMath::FloorToInt(Position.Y / CentralCesiumComponentGridSizeCm));
}

bool IsCesiumQueryComponent(
    const UPrimitiveComponent* Component,
    const UWorld* World)
{
    return IsValid(Component) &&
        Component->GetWorld() == World &&
        Component->IsRegistered() &&
        Component->IsVisible() &&
        Component->IsQueryCollisionEnabled() &&
        Component->GetClass()->GetName().Contains(
            TEXT("CesiumGltfPrimitiveComponent"));
}

int32 GetCentralGatePopulation(const EOpenMassCrowdCentralPopulationGate Gate)
{
    int32 RequestedPopulation = 0;
    switch (Gate)
    {
    case EOpenMassCrowdCentralPopulationGate::Gate30:
        RequestedPopulation = 30;
        break;
    case EOpenMassCrowdCentralPopulationGate::Gate50:
        RequestedPopulation = 50;
        break;
    case EOpenMassCrowdCentralPopulationGate::Gate100:
        RequestedPopulation = 100;
        break;
    case EOpenMassCrowdCentralPopulationGate::Gate200:
        RequestedPopulation = 200;
        break;
    case EOpenMassCrowdCentralPopulationGate::Gate300:
        RequestedPopulation = 300;
        break;
    default:
        return 0;
    }
    // Gate200/Gate300 remain readable for old saved actors, but the current
    // experience contract deliberately caps the active population at 100.
    return FMath::Min(RequestedPopulation, FullCentralPopulation);
}

int32 GetCentralFullDistrictPopulation(const int32 DistrictIndex)
{
    return FullCentralPopulation / RequiredCentralSpawnDistrictCount +
        (DistrictIndex <
            FullCentralPopulation % RequiredCentralSpawnDistrictCount
            ? 1
            : 0);
}

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

/**
 * FZoneGraphBVTree::Build is public in UE 5.7 but is not exported from the
 * ZoneGraph DLL. Central can contain thousands of lane-zones, so the bounded
 * demo's flat leaf array is not suitable: reproduce the engine's balanced
 * preorder layout locally and keep spatial queries logarithmic.
 */
struct FOpenMassCrowdBalancedZoneBVTree final : public FZoneGraphBVTree
{
    static int32 GetLongestAxis(const FZoneGraphBVNode& Node)
    {
        const uint16 Dimensions[] = {
            static_cast<uint16>(Node.MaxX - Node.MinX),
            static_cast<uint16>(Node.MaxY - Node.MinY),
            static_cast<uint16>(Node.MaxZ - Node.MinZ)
        };
        return Dimensions[0] > Dimensions[1]
            ? (Dimensions[0] > Dimensions[2] ? 0 : 2)
            : (Dimensions[1] > Dimensions[2] ? 1 : 2);
    }

    static uint16 GetAxisMinimum(const FZoneGraphBVNode& Node, const int32 Axis)
    {
        return Axis == 0 ? Node.MinX : (Axis == 1 ? Node.MinY : Node.MinZ);
    }

    static FZoneGraphBVNode CalculateRangeBounds(
        const TArray<FZoneGraphBVNode>& Items,
        const int32 BeginIndex,
        const int32 EndIndex)
    {
        FZoneGraphBVNode Result = Items[BeginIndex];
        for (int32 ItemIndex = BeginIndex + 1; ItemIndex < EndIndex; ++ItemIndex)
        {
            const FZoneGraphBVNode& Item = Items[ItemIndex];
            Result.MinX = FMath::Min(Result.MinX, Item.MinX);
            Result.MinY = FMath::Min(Result.MinY, Item.MinY);
            Result.MinZ = FMath::Min(Result.MinZ, Item.MinZ);
            Result.MaxX = FMath::Max(Result.MaxX, Item.MaxX);
            Result.MaxY = FMath::Max(Result.MaxY, Item.MaxY);
            Result.MaxZ = FMath::Max(Result.MaxZ, Item.MaxZ);
        }
        return Result;
    }

    void Subdivide(
        TArray<FZoneGraphBVNode>& Items,
        const int32 BeginIndex,
        const int32 EndIndex)
    {
        const int32 Count = EndIndex - BeginIndex;
        const int32 CurrentNodeIndex = Nodes.AddDefaulted();
        if (Count == 1)
        {
            Nodes[CurrentNodeIndex] = Items[BeginIndex];
            return;
        }

        Nodes[CurrentNodeIndex] = CalculateRangeBounds(Items, BeginIndex, EndIndex);
        const int32 Axis = GetLongestAxis(Nodes[CurrentNodeIndex]);
        Algo::Sort(
            MakeArrayView(Items.GetData() + BeginIndex, Count),
            [Axis](const FZoneGraphBVNode& A, const FZoneGraphBVNode& B)
            {
                const uint16 MinimumA = GetAxisMinimum(A, Axis);
                const uint16 MinimumB = GetAxisMinimum(B, Axis);
                return MinimumA == MinimumB ? A.Index < B.Index : MinimumA < MinimumB;
            });

        const int32 SplitIndex = BeginIndex + Count / 2;
        Subdivide(Items, BeginIndex, SplitIndex);
        Subdivide(Items, SplitIndex, EndIndex);
        Nodes[CurrentNodeIndex].Index = -(Nodes.Num() - CurrentNodeIndex);
    }

    void BuildBalanced(const TArray<FZoneData>& Zones)
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

        TArray<FZoneGraphBVNode> Items;
        Items.Reserve(Zones.Num());
        for (int32 ZoneIndex = 0; ZoneIndex < Zones.Num(); ++ZoneIndex)
        {
            FZoneGraphBVNode& Item = Items.AddDefaulted_GetRef();
            Item = CalcNodeBounds(Zones[ZoneIndex].Bounds);
            Item.Index = ZoneIndex;
        }

        Nodes.Reserve(Items.Num() * 2 - 1);
        Subdivide(Items, 0, Items.Num());
    }
};

bool IsStrictCentralStableId(const FName Name)
{
    if (Name.IsNone())
    {
        return false;
    }

    const FString Value = Name.ToString();
    if (Value.IsEmpty() || Value.Len() > 160)
    {
        return false;
    }

    const auto IsLowerAlphaNumeric = [](const TCHAR Character)
    {
        return (Character >= TEXT('a') && Character <= TEXT('z')) ||
            (Character >= TEXT('0') && Character <= TEXT('9'));
    };
    if (!IsLowerAlphaNumeric(Value[0]))
    {
        return false;
    }

    for (const TCHAR Character : Value)
    {
        if (!IsLowerAlphaNumeric(Character) &&
            Character != TEXT('.') &&
            Character != TEXT('_') &&
            Character != TEXT('-'))
        {
            return false;
        }
    }
    return true;
}

bool IsStrictCentralSha256(const FString& Value)
{
    if (Value.Len() != 64)
    {
        return false;
    }
    for (const TCHAR Character : Value)
    {
        if (!((Character >= TEXT('0') && Character <= TEXT('9')) ||
              (Character >= TEXT('a') && Character <= TEXT('f'))))
        {
            return false;
        }
    }
    return true;
}

bool HasStrictCentralHashes(
    const FOpenMassCrowdCentralNetworkHashes& Hashes,
    const bool bRequireCellContent)
{
    return Hashes.HashAlgorithm == TEXT("SHA-256") &&
        IsStrictCentralSha256(Hashes.TopologySha256) &&
        IsStrictCentralSha256(Hashes.GeoreferenceSha256) &&
        IsStrictCentralSha256(Hashes.TilesetSha256) &&
        IsStrictCentralSha256(Hashes.CollisionSettingsSha256) &&
        (!bRequireCellContent || IsStrictCentralSha256(Hashes.CellContentSha256)) &&
        IsStrictCentralSha256(Hashes.CombinedSha256);
}

bool HasMatchingCentralCompatibilityInputs(
    const FOpenMassCrowdCentralNetworkHashes& CellHashes,
    const FOpenMassCrowdCentralNetworkHashes& NetworkHashes)
{
    return CellHashes.HashAlgorithm == NetworkHashes.HashAlgorithm &&
        CellHashes.TopologySha256 == NetworkHashes.TopologySha256 &&
        CellHashes.GeoreferenceSha256 == NetworkHashes.GeoreferenceSha256 &&
        CellHashes.TilesetSha256 == NetworkHashes.TilesetSha256 &&
        CellHashes.CollisionSettingsSha256 == NetworkHashes.CollisionSettingsSha256;
}

bool IsFiniteCentralBox(const FBox& Bounds)
{
    return Bounds.IsValid != 0 &&
        !Bounds.Min.ContainsNaN() &&
        !Bounds.Max.ContainsNaN();
}

/** Central-only A* filter. Local continues to use UE's stock distance filter. */
struct FOpenMassCrowdCentralPathFilter
{
    using FNodeRef = FZoneGraphAStarWrapper::FNodeRef;

    FOpenMassCrowdCentralPathFilter(
        const FZoneGraphStorage& InStorage,
        const FZoneGraphLaneLocation& InStart,
        const FZoneGraphLaneLocation& InEnd,
        const TArray<int32>& InOccupancies,
        const TArray<int32>& InReverseLaneIndices,
        const TArray<uint8>& InCrossingFlags,
        const TArray<uint8>& InAvailabilityFlags,
        const TArray<int32>& InRecentLaneIndices)
        : BaseFilter(InStorage, InStart, InEnd)
        , Storage(InStorage)
        , StartLaneIndex(InStart.LaneHandle.Index)
        , Occupancies(InOccupancies)
        , ReverseLaneIndices(InReverseLaneIndices)
        , CrossingFlags(InCrossingFlags)
        , AvailabilityFlags(InAvailabilityFlags)
        , RecentLaneIndices(InRecentLaneIndices)
    {
    }

    bool IsStart(const FZoneGraphAStarNode& Node) const { return BaseFilter.IsStart(Node); }
    bool IsEnd(const FZoneGraphAStarNode& Node) const { return BaseFilter.IsEnd(Node); }
    FVector::FReal GetHeuristicScale() const { return BaseFilter.GetHeuristicScale(); }

    FVector::FReal GetHeuristicCost(
        const FZoneGraphAStarNode& NeighbourNode,
        const FZoneGraphAStarNode& EndNode) const
    {
        return BaseFilter.GetHeuristicCost(NeighbourNode, EndNode);
    }

    FVector::FReal GetTraversalCost(
        const FZoneGraphAStarNode& CurNode,
        const FZoneGraphAStarNode& NeighbourNode) const
    {
        FVector::FReal Cost = BaseFilter.GetTraversalCost(CurNode, NeighbourNode);
        if (!FMath::IsFinite(Cost) || Cost >= TNumericLimits<FVector::FReal>::Max())
        {
            return Cost;
        }

        const int32 LaneIndex = NeighbourNode.NodeRef;
        float LaneLength = 1.0f;
        UE::ZoneGraph::Query::GetLaneLength(Storage, LaneIndex, LaneLength);
        const float Capacity = FMath::Max(1.0f, LaneLength / 140.0f);
        const float DensityRatio = Occupancies.IsValidIndex(LaneIndex)
            ? static_cast<float>(Occupancies[LaneIndex]) / Capacity
            : 0.0f;
        Cost *= 1.0 + FMath::Clamp(DensityRatio, 0.0f, 4.0f) * 0.65f;

        // A crossing is an explicit certified lane. Prefer it over a similarly
        // long non-crossing detour without reducing cost below metric distance.
        if (!CrossingFlags.IsValidIndex(LaneIndex) || CrossingFlags[LaneIndex] == 0)
        {
            Cost += 25.0;
        }

        if (ReverseLaneIndices.IsValidIndex(CurNode.NodeRef) &&
            ReverseLaneIndices[CurNode.NodeRef] == LaneIndex)
        {
            Cost += 100000.0;
        }

        const int32 HistoryIndex = RecentLaneIndices.FindLast(LaneIndex);
        if (HistoryIndex != INDEX_NONE)
        {
            const int32 Age = RecentLaneIndices.Num() - 1 - HistoryIndex;
            Cost += 1200.0 / static_cast<double>(Age + 1);
        }
        return Cost;
    }

    bool IsTraversalAllowed(const FNodeRef CurrentLaneIndex, const FNodeRef& Neighbour) const
    {
        // A certified pedestrian lane can legitimately end at a cul-de-sac.
        // In that case its reverse lane is the only supported continuation, so
        // rejecting an initial U-turn would make the whole real component look
        // unreachable. GetTraversalCost still applies the very high anti-U-turn
        // penalty, which means A* selects this transition only when no ordinary
        // junction continuation is available; no synthetic edge is introduced.
        return AvailabilityFlags.IsValidIndex(Neighbour) &&
            AvailabilityFlags[Neighbour] != 0 &&
            BaseFilter.IsTraversalAllowed(CurrentLaneIndex, Neighbour);
    }

    bool WantsPartialSolution() const { return false; }
    bool ShouldIncludeStartNodeInPath() const { return true; }

private:
    FZoneGraphPathFilter BaseFilter;
    const FZoneGraphStorage& Storage;
    int32 StartLaneIndex = INDEX_NONE;
    const TArray<int32>& Occupancies;
    const TArray<int32>& ReverseLaneIndices;
    const TArray<uint8>& CrossingFlags;
    const TArray<uint8>& AvailabilityFlags;
    const TArray<int32>& RecentLaneIndices;
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

    // A dedicated batch keeps every current person/station association alive
    // between refreshes. Unlike short-lived DrawDebugLine calls, this layer
    // cannot flicker when the editor or GPU misses a 100 ms refresh window.
    InvestorAssociationLineBatch =
        CreateDefaultSubobject<ULineBatchComponent>(
            TEXT("InvestorAssociationLineBatch"));
    InvestorAssociationLineBatch->SetupAttachment(SceneRoot);
    InvestorAssociationLineBatch->SetCollisionEnabled(
        ECollisionEnabled::NoCollision);
    InvestorAssociationLineBatch->SetGenerateOverlapEvents(false);
    InvestorAssociationLineBatch->SetCanEverAffectNavigation(false);
    InvestorAssociationLineBatch->SetCastShadow(false);
    InvestorAssociationLineBatch->bReceivesDecals = false;
    InvestorAssociationLineBatch->bCalculateAccurateBounds = true;
    // All association lines use infinite lifetime and are explicitly replaced
    // as one batch. The component does not need to scan ~2,000 line segments
    // every frame for lifetime expiry.
    InvestorAssociationLineBatch->PrimaryComponentTick.bCanEverTick = false;
    InvestorAssociationLineBatch->SetComponentTickEnabled(false);
    InvestorAssociationLineBatch->ComponentTags.AddUnique(
        TEXT("TelecomTwinAssociationLines"));
    InvestorAssociationLineBatch->SetVisibility(false);

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

int32 AOpenMassCrowdSpawner::GetRequestedCentralPopulation() const
{
    if (bInvestorDeliveryDemoEnabled)
    {
        return FMath::Clamp(
            InvestorDeliveryPopulation,
            1,
            FullCentralPopulation);
    }
    return GetCentralGatePopulation(CentralPopulationGate);
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

    if (bInvestorDeliveryDemoEnabled)
    {
        RegisterInvestorLegacySignalGuards();
    }

    if (NetworkMode == EOpenMassCrowdNetworkMode::CentralCertifiedCache)
    {
        // Only a new world/PIE session may clear lifetime collision evidence.
        // Population retries below reset current-state arrays but retain every
        // earlier attempt's peak/count/minimum.
        ResetCentralSessionTelemetry();
    }

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
    GetWorldTimerManager().ClearTimer(CentralAdmissionTimer);
    HideCentralProfile();
    HideInvestorKPI();
    UnregisterInvestorLegacySignalGuards();
    DestroyRuntimePopulation();
    Super::EndPlay(EndPlayReason);
}

void AOpenMassCrowdSpawner::Tick(const float DeltaSeconds)
{
    Super::Tick(DeltaSeconds);

    if (!bInvestorDeliveryDemoEnabled)
    {
        if (bInvestorDemoInitialized)
        {
            HideInvestorKPI();
            ClearInvestorAssociationVisuals();
            bInvestorDemoInitialized = false;
            InvestorPeople.Reset();
            InvestorStations.Reset();
        }
        UnregisterInvestorLegacySignalGuards();
    }

    if (SpawnedEntities.IsEmpty())
    {
        return;
    }

    if (NetworkMode == EOpenMassCrowdNetworkMode::CentralCertifiedCache)
    {
        const double ApplicationDeltaSeconds = FApp::GetDeltaTime();
        const float TelemetryDeltaSeconds =
            FMath::IsFinite(ApplicationDeltaSeconds) &&
                ApplicationDeltaSeconds > 0.0
            ? static_cast<float>(ApplicationDeltaSeconds)
            : FMath::Max(DeltaSeconds, 0.0f);
        RecordCentralFrameTime(TelemetryDeltaSeconds);
        CentralTelemetrySampleAccumulator += TelemetryDeltaSeconds;
    }

    // A ZoneGraph short path can be partial (its fixed point buffer is smaller
    // than a sampled lane). Continue it on the first PostUpdateWork tick in
    // which Mass marks it done so pedestrians do not pause for a polling timer.
    PathRefreshAccumulator = 0.0f;
    if (NetworkMode == EOpenMassCrowdNetworkMode::CentralCertifiedCache)
    {
        ProcessCentralConflictReplans();
        RefreshCentralUnavailableRoutes();
    }
    RefreshCompletedPaths();

    // Central constraint and liveness recovery run in the certified Mass
    // transform processor after PathFollow/ApplyMovement. Running them here in
    // the actor tick occurs before Mass PrePhysics and lets PathFollow overwrite
    // the recovered lane progress in the same frame.

    GroundCorrectionAccumulator += DeltaSeconds;
    // Live traces are a bounded streaming-validity guard.  Visual transforms
    // already follow the cached 10 cm certified lane every frame above, so
    // guard staggering cannot introduce actor stutter or speculative VAT poses.
    const float EffectiveGroundCorrectionInterval =
        bInvestorDeliveryDemoEnabled &&
            NetworkMode == EOpenMassCrowdNetworkMode::CentralCertifiedCache
        ? 0.05f
        : FMath::Min(GroundCorrectionInterval, 0.05f);
    if (GroundCorrectionAccumulator >= EffectiveGroundCorrectionInterval)
    {
        GroundCorrectionAccumulator = 0.0f;
        CorrectMassGrounding();
    }

    if (NetworkMode == EOpenMassCrowdNetworkMode::CentralCertifiedCache &&
        bInvestorDeliveryDemoEnabled && bCentralAdmissionReleased)
    {
        EnsureInvestorDemoInitialized();
        UpdateInvestorDemo(DeltaSeconds);
    }

    // Spawned actor representations are positioned when Mass creates or swaps
    // them, but UE 5.7 does not continuously copy FTransformFragment back to a
    // plain AActor.  Our official City Sample character is a child of that
    // lightweight actor, so keep the visible 30-person demo in lock-step with
    // the Mass/ZoneGraph simulation.
    SyncVisualActorsToMass();
    if (NetworkMode == EOpenMassCrowdNetworkMode::CentralCertifiedCache)
    {
        RecordCentralTelemetry();
        UpdateCentralProfileInteraction();
    }
}

void AOpenMassCrowdSpawner::RefreshCesiumGroundComponentCache() const
{
    UWorld* World = GetWorld();
    if (NetworkMode != EOpenMassCrowdNetworkMode::CentralCertifiedCache || !World)
    {
        return;
    }

    const double WorldTime = World->GetTimeSeconds();
    if (bCesiumGroundComponentCacheInitialized &&
        WorldTime >= CesiumGroundComponentCacheRefreshWorldTime &&
        WorldTime - CesiumGroundComponentCacheRefreshWorldTime <
            CentralCesiumComponentCacheRefreshSeconds)
    {
        return;
    }

    CesiumGroundComponentGrid.Reset();
    CesiumGroundLargeComponents.Reset();
    int32 CachedComponentCount = 0;
    for (TObjectIterator<UPrimitiveComponent> It; It; ++It)
    {
        UPrimitiveComponent* Component = *It;
        if (!IsCesiumQueryComponent(Component, World))
        {
            continue;
        }

        const FBox Bounds = Component->Bounds.GetBox();
        if (Bounds.IsValid == 0 || Bounds.Min.ContainsNaN() || Bounds.Max.ContainsNaN())
        {
            continue;
        }
        const FIntPoint MinimumBucket = GetCesiumGroundGridCoordinate(Bounds.Min);
        const FIntPoint MaximumBucket = GetCesiumGroundGridCoordinate(Bounds.Max);
        const int64 BucketCountX =
            static_cast<int64>(MaximumBucket.X) - MinimumBucket.X + 1;
        const int64 BucketCountY =
            static_cast<int64>(MaximumBucket.Y) - MinimumBucket.Y + 1;
        const int64 BucketCount = BucketCountX * BucketCountY;
        if (BucketCountX <= 0 || BucketCountY <= 0 ||
            BucketCount > CentralCesiumMaximumBucketsPerComponent)
        {
            CesiumGroundLargeComponents.Add(Component);
            ++CachedComponentCount;
            continue;
        }

        for (int32 GridX = MinimumBucket.X; GridX <= MaximumBucket.X; ++GridX)
        {
            for (int32 GridY = MinimumBucket.Y; GridY <= MaximumBucket.Y; ++GridY)
            {
                CesiumGroundComponentGrid.FindOrAdd(
                    FIntPoint(GridX, GridY)).Add(Component);
            }
        }
        ++CachedComponentCount;
    }

    CesiumGroundComponentCacheRefreshWorldTime = WorldTime;
    bCesiumGroundComponentCacheInitialized = true;
    ++CentralGroundComponentCacheRefreshCount;
    UE_LOG(
        LogTemp,
        Verbose,
        TEXT("OPEN_MASS_CROWD_CESIUM_COMPONENT_CACHE refresh=%d components=%d buckets=%d large=%d"),
        CentralGroundComponentCacheRefreshCount,
        CachedComponentCount,
        CesiumGroundComponentGrid.Num(),
        CesiumGroundLargeComponents.Num());
}

void AOpenMassCrowdSpawner::GatherSpatiallyRelevantCesiumComponents(
    const FVector& XYPoint,
    TArray<UPrimitiveComponent*>& OutComponents) const
{
    OutComponents.Reset();
    RefreshCesiumGroundComponentCache();
    UWorld* World = GetWorld();
    if (!World || !bCesiumGroundComponentCacheInitialized)
    {
        return;
    }

    const auto AddIfRelevant = [World, &XYPoint, &OutComponents](
        const TWeakObjectPtr<UPrimitiveComponent>& WeakComponent)
    {
        UPrimitiveComponent* Component = WeakComponent.Get();
        if (!IsCesiumQueryComponent(Component, World))
        {
            return;
        }
        const FBox Bounds = Component->Bounds.GetBox();
        if (XYPoint.X < Bounds.Min.X || XYPoint.X > Bounds.Max.X ||
            XYPoint.Y < Bounds.Min.Y || XYPoint.Y > Bounds.Max.Y)
        {
            return;
        }
        OutComponents.Add(Component);
    };

    if (const TArray<TWeakObjectPtr<UPrimitiveComponent>>* Bucket =
        CesiumGroundComponentGrid.Find(GetCesiumGroundGridCoordinate(XYPoint)))
    {
        for (const TWeakObjectPtr<UPrimitiveComponent>& Component : *Bucket)
        {
            AddIfRelevant(Component);
        }
    }
    for (const TWeakObjectPtr<UPrimitiveComponent>& Component :
        CesiumGroundLargeComponents)
    {
        AddIfRelevant(Component);
    }
}

bool AOpenMassCrowdSpawner::ProjectToCesiumGround(
    const FVector& XYPoint,
    FVector& OutGroundPoint) const
{
    return ProjectToCesiumGroundClassified(XYPoint, OutGroundPoint) ==
        ECesiumGroundProjectionResult::Accepted;
}

AOpenMassCrowdSpawner::ECesiumGroundProjectionResult
AOpenMassCrowdSpawner::ProjectToCesiumGroundClassified(
    const FVector& XYPoint,
    FVector& OutGroundPoint) const
{
    if (!GetWorld())
    {
        return ECesiumGroundProjectionResult::NoRawSupport;
    }

    // XYPoint.Z is the locally expected elevation.  Initial probes pass the
    // spawner Z, while lane and correction probes pass their previous/current
    // grounded Z so a walkable hill can accumulate beyond GroundTolerance.
    const float ExpectedElevation = XYPoint.Z;

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
    // return early. Central uses a periodically refreshed XY grid so one guard
    // never traverses every streamed component. Local retains its proven scan.
    const auto ConsiderComponent =
        [&](UPrimitiveComponent* Component)
    {
        if (!IsCesiumQueryComponent(Component, GetWorld()))
        {
            return;
        }
        if (NetworkMode == EOpenMassCrowdNetworkMode::CentralCertifiedCache)
        {
            ++CentralGroundCandidateComponentTestCount;
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
    };
    if (NetworkMode == EOpenMassCrowdNetworkMode::CentralCertifiedCache)
    {
        TArray<UPrimitiveComponent*> RelevantComponents;
        GatherSpatiallyRelevantCesiumComponents(XYPoint, RelevantComponents);
        for (UPrimitiveComponent* Component : RelevantComponents)
        {
            ConsiderComponent(Component);
        }
    }
    else
    {
        for (TObjectIterator<UPrimitiveComponent> It; It; ++It)
        {
            ConsiderComponent(*It);
        }
    }

    // Apply Cesium ownership, walkability and elevation checks only after
    // selecting the global raw first blocker. Skipping a closer wall/roof and
    // accepting a hidden flat triangle below it would be collision tunnelling.
    if (!bFoundRawHit)
    {
        return ECesiumGroundProjectionResult::NoRawSupport;
    }
    // Publish the raw exact-XY blocker position for diagnostics even when the
    // caller must reject its owner, normal or elevation. Callers still consume
    // this position only when the classified result is Accepted.
    OutGroundPoint = FVector(
        XYPoint.X,
        XYPoint.Y,
        NearestHit.ImpactPoint.Z);
    const UPrimitiveComponent* NearestComponent =
        NearestHit.GetComponent();
    if (!NearestComponent ||
        !NearestComponent->GetClass()->GetName().Contains(
            TEXT("CesiumGltfPrimitiveComponent")))
    {
        return ECesiumGroundProjectionResult::NonCesiumFirstBlocker;
    }
    if (!FMath::IsFinite(NearestHit.ImpactNormal.Z) ||
        NearestHit.ImpactNormal.Z < MinWalkableSurfaceNormalZ)
    {
        return ECesiumGroundProjectionResult::UnwalkableFirstBlocker;
    }
    if (!FMath::IsFinite(ExpectedElevation) ||
        !FMath::IsFinite(NearestHit.ImpactPoint.Z) ||
        FMath::Abs(
            NearestHit.ImpactPoint.Z - ExpectedElevation) > GroundTolerance)
    {
        return ECesiumGroundProjectionResult::ElevationMismatch;
    }

    return ECesiumGroundProjectionResult::Accepted;
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

bool AOpenMassCrowdSpawner::BuildRuntimeZoneGraphFromCentralCache()
{
    const auto RejectCache = [](const FString& Reason)
    {
        UE_LOG(
            LogTemp,
            Error,
            TEXT("OPEN_MASS_CROWD_CENTRAL_CACHE_REJECT reason=%s"),
            *Reason);
        return false;
    };

    UZoneGraphSubsystem* ZoneGraphSubsystem =
        UWorld::GetSubsystem<UZoneGraphSubsystem>(GetWorld());
    if (!ZoneGraphSubsystem)
    {
        return RejectCache(TEXT("zone_graph_subsystem_unavailable"));
    }
    if (!IsValid(CentralNetworkAsset))
    {
        return RejectCache(TEXT("asset_missing"));
    }

    const UOpenMassCrowdCentralNetworkDataAsset& Asset = *CentralNetworkAsset;
    if (!Asset.IsSchemaVersionSupported() ||
        Asset.SchemaVersion != UOpenMassCrowdCentralNetworkDataAsset::CurrentSchemaVersion)
    {
        return RejectCache(FString::Printf(
            TEXT("unsupported_schema actual=%d expected=%d"),
            Asset.SchemaVersion,
            UOpenMassCrowdCentralNetworkDataAsset::CurrentSchemaVersion));
    }
    if (!IsStrictCentralStableId(Asset.NetworkId) ||
        !Asset.BuildId.IsValid() ||
        Asset.GeneratorVersion.IsEmpty() ||
        !Asset.bGroundOnlyNetwork ||
        !IsStrictCentralSha256(Asset.ParentCertifiedSha256) ||
        !IsStrictCentralSha256(Asset.GroundOnlyPolicySha256) ||
        Asset.GroundOnlyExcludedSourceFeatureCount <= 0 ||
        !IsFiniteCentralBox(Asset.WorldBounds) ||
        Asset.Cells.IsEmpty() ||
        Asset.Components.IsEmpty())
    {
        return RejectCache(TEXT("invalid_asset_identity_or_bounds"));
    }
    if (!Asset.HasCompleteCompatibilityHashes() ||
        !HasStrictCentralHashes(Asset.Hashes, true))
    {
        return RejectCache(TEXT("invalid_network_hashes"));
    }
    if (Asset.Evidence.WholeAreaRecertificationCount != 0)
    {
        return RejectCache(TEXT("cache_records_runtime_recertification"));
    }

    constexpr int32 RequiredGroundEvidenceMask =
        static_cast<int32>(EOpenMassCrowdCentralGroundEvidence::ExactXYSupport) |
        static_cast<int32>(EOpenMassCrowdCentralGroundEvidence::FirstBlocker) |
        static_cast<int32>(EOpenMassCrowdCentralGroundEvidence::HeightContinuity) |
        static_cast<int32>(EOpenMassCrowdCentralGroundEvidence::Slope) |
        static_cast<int32>(EOpenMassCrowdCentralGroundEvidence::MultiTrackSupport) |
        static_cast<int32>(EOpenMassCrowdCentralGroundEvidence::CapsuleClearance);
    const float MaximumSurfaceSlopeDegrees =
        FMath::RadiansToDegrees(FMath::Acos(MinWalkableSurfaceNormalZ));

    TMap<FName, const FOpenMassCrowdCentralCell*> CellsById;
    TSet<FIntPoint> GridCoordinates;
    TMap<FName, const FOpenMassCrowdCentralNode*> NodesById;
    TMap<FName, const FOpenMassCrowdCentralDirectedLane*> LanesById;
    TMap<FName, const FOpenMassCrowdCentralPortal*> PortalsById;
    TMap<FName, const FOpenMassCrowdCentralComponent*> ComponentsById;
    TArray<const FOpenMassCrowdCentralDirectedLane*> CertifiedLanes;
    int32 TotalGroundSampleCount = 0;
    int32 TotalPortalCount = 0;
    double TotalDirectionalLaneLengthCm = 0.0;

    for (const FOpenMassCrowdCentralComponent& Component : Asset.Components)
    {
        if (!Component.bCertified ||
            !IsStrictCentralStableId(Component.ComponentId) ||
            Component.CellIds.IsEmpty() ||
            Component.NodeIds.Num() < 2 ||
            Component.DirectedLaneIds.Num() < 2 ||
            !FMath::IsFinite(Component.DirectionalLaneLengthCm) ||
            Component.DirectionalLaneLengthCm <= 0.0 ||
            Component.JunctionCount < 0 ||
            Component.StreetBlockCount < 0 ||
            ComponentsById.Contains(Component.ComponentId))
        {
            return RejectCache(TEXT("invalid_certified_component"));
        }
        ComponentsById.Add(Component.ComponentId, &Component);
    }

    for (const FOpenMassCrowdCentralCell& Cell : Asset.Cells)
    {
        if (Cell.SchemaVersion != UOpenMassCrowdCentralNetworkDataAsset::CurrentSchemaVersion ||
            !Cell.bCertified ||
            !IsStrictCentralStableId(Cell.CellId) ||
            !IsFiniteCentralBox(Cell.WorldBounds) ||
            Cell.SourceFeatureIds.IsEmpty() ||
            Cell.Nodes.IsEmpty() ||
            Cell.DirectedLanes.IsEmpty())
        {
            return RejectCache(FString::Printf(
                TEXT("invalid_cell cell=%s"),
                *Cell.CellId.ToString()));
        }
        if (CellsById.Contains(Cell.CellId) || GridCoordinates.Contains(Cell.GridCoordinate))
        {
            return RejectCache(FString::Printf(
                TEXT("duplicate_cell_identity cell=%s grid=(%d,%d)"),
                *Cell.CellId.ToString(),
                Cell.GridCoordinate.X,
                Cell.GridCoordinate.Y));
        }
        if (!HasStrictCentralHashes(Cell.Hashes, true) ||
            !HasMatchingCentralCompatibilityInputs(Cell.Hashes, Asset.Hashes))
        {
            return RejectCache(FString::Printf(
                TEXT("stale_or_invalid_cell_hash cell=%s"),
                *Cell.CellId.ToString()));
        }
        CellsById.Add(Cell.CellId, &Cell);
        GridCoordinates.Add(Cell.GridCoordinate);

        TSet<FName> CellSourceFeatureIds;
        for (const FName SourceFeatureId : Cell.SourceFeatureIds)
        {
            if (!IsStrictCentralStableId(SourceFeatureId) ||
                CellSourceFeatureIds.Contains(SourceFeatureId))
            {
                return RejectCache(FString::Printf(
                    TEXT("invalid_source_feature_reference cell=%s feature=%s"),
                    *Cell.CellId.ToString(),
                    *SourceFeatureId.ToString()));
            }
            CellSourceFeatureIds.Add(SourceFeatureId);
        }

        for (const FOpenMassCrowdCentralNode& Node : Cell.Nodes)
        {
            if (!IsStrictCentralStableId(Node.NodeId) ||
                Node.CellId != Cell.CellId ||
                !ComponentsById.Contains(Node.ComponentId) ||
                Node.Position.ContainsNaN() ||
                NodesById.Contains(Node.NodeId))
            {
                return RejectCache(FString::Printf(
                    TEXT("invalid_or_duplicate_node cell=%s node=%s"),
                    *Cell.CellId.ToString(),
                    *Node.NodeId.ToString()));
            }
            NodesById.Add(Node.NodeId, &Node);
        }

        int32 CellGroundSampleCount = 0;
        double CellDirectionalLaneLengthCm = 0.0;
        for (const FOpenMassCrowdCentralDirectedLane& Lane : Cell.DirectedLanes)
        {
            if (!Lane.bCertified ||
                !Lane.bGroundOnlyEligible ||
                !IsStrictCentralStableId(Lane.LaneId) ||
                Lane.CellId != Cell.CellId ||
                !ComponentsById.Contains(Lane.ComponentId) ||
                !IsStrictCentralStableId(Lane.SourceFeatureId) ||
                !CellSourceFeatureIds.Contains(Lane.SourceFeatureId) ||
                !IsStrictCentralStableId(Lane.FromNodeId) ||
                !IsStrictCentralStableId(Lane.ToNodeId) ||
                Lane.FromNodeId == Lane.ToNodeId ||
                !IsStrictCentralStableId(Lane.ReverseLaneId) ||
                (Lane.TopologyOrigin != EOpenMassCrowdCentralTopologyOrigin::OpenStreetMap &&
                 Lane.TopologyOrigin != EOpenMassCrowdCentralTopologyOrigin::OsmSemanticRecovery) ||
                Lane.PedestrianClass == EOpenMassCrowdCentralPedestrianClass::Unknown ||
                !FMath::IsFinite(Lane.WidthCm) ||
                Lane.WidthCm < PedestrianClearanceRadius * 2.0f ||
                !FMath::IsFinite(Lane.LengthCm) ||
                Lane.LengthCm <= KINDA_SMALL_NUMBER ||
                Lane.GroundSamples.Num() < 2 ||
                LanesById.Contains(Lane.LaneId))
            {
                return RejectCache(FString::Printf(
                    TEXT("invalid_or_uncertified_lane cell=%s lane=%s"),
                    *Cell.CellId.ToString(),
                    *Lane.LaneId.ToString()));
            }

            TSet<FName> SampleIds;
            double PreviousDistanceAlongLaneCm = -1.0;
            double GeometryLengthCm = 0.0;
            for (int32 SampleIndex = 0; SampleIndex < Lane.GroundSamples.Num(); ++SampleIndex)
            {
                const FOpenMassCrowdCentralGroundSample& Sample =
                    Lane.GroundSamples[SampleIndex];
                const double NormalSizeSquared = Sample.SurfaceNormal.SizeSquared();
                if (!IsStrictCentralStableId(Sample.SampleId) ||
                    SampleIds.Contains(Sample.SampleId) ||
                    Sample.SampleIndex != SampleIndex ||
                    !FMath::IsFinite(Sample.DistanceAlongLaneCm) ||
                    Sample.DistanceAlongLaneCm < 0.0 ||
                    Sample.CenterPosition.ContainsNaN() ||
                    Sample.LeftTrackPosition.ContainsNaN() ||
                    Sample.RightTrackPosition.ContainsNaN() ||
                    Sample.SurfaceNormal.ContainsNaN() ||
                    !FMath::IsFinite(NormalSizeSquared) ||
                    !FMath::IsNearlyEqual(NormalSizeSquared, 1.0, 0.05) ||
                    Sample.SurfaceNormal.Z < MinWalkableSurfaceNormalZ ||
                    !FMath::IsFinite(Sample.SurfaceSlopeDegrees) ||
                    Sample.SurfaceSlopeDegrees < 0.0f ||
                    Sample.SurfaceSlopeDegrees > MaximumSurfaceSlopeDegrees + 0.1f ||
                    !FMath::IsFinite(Sample.MaxNeighborHeightDeltaCm) ||
                    Sample.MaxNeighborHeightDeltaCm < 0.0f ||
                    Sample.MaxNeighborHeightDeltaCm > MaxPedestrianSampleHeightDelta ||
                    Sample.SupportingPrimitiveId.IsNone() ||
                    Sample.EvidenceMask != RequiredGroundEvidenceMask ||
                    FVector::Dist2D(Sample.CenterPosition, Sample.LeftTrackPosition) <= 1.0f ||
                    FVector::Dist2D(Sample.CenterPosition, Sample.RightTrackPosition) <= 1.0f)
                {
                    return RejectCache(FString::Printf(
                        TEXT("invalid_ground_sample lane=%s sample=%d"),
                        *Lane.LaneId.ToString(),
                        SampleIndex));
                }

                if ((SampleIndex == 0 &&
                     !FMath::IsNearlyZero(Sample.DistanceAlongLaneCm, 0.1)) ||
                    (SampleIndex > 0 &&
                     Sample.DistanceAlongLaneCm <= PreviousDistanceAlongLaneCm))
                {
                    return RejectCache(FString::Printf(
                        TEXT("unordered_ground_samples lane=%s sample=%d"),
                        *Lane.LaneId.ToString(),
                        SampleIndex));
                }
                if (SampleIndex > 0)
                {
                    const double SegmentLengthCm = FVector::Distance(
                        Lane.GroundSamples[SampleIndex - 1].CenterPosition,
                        Sample.CenterPosition);
                    if (!FMath::IsFinite(SegmentLengthCm) ||
                        SegmentLengthCm <= KINDA_SMALL_NUMBER)
                    {
                        return RejectCache(FString::Printf(
                            TEXT("degenerate_ground_segment lane=%s sample=%d"),
                            *Lane.LaneId.ToString(),
                            SampleIndex));
                    }
                    GeometryLengthCm += SegmentLengthCm;
                }

                SampleIds.Add(Sample.SampleId);
                PreviousDistanceAlongLaneCm = Sample.DistanceAlongLaneCm;
            }

            const double LengthToleranceCm = FMath::Max(2.0, Lane.LengthCm * 0.01);
            if (!FMath::IsNearlyEqual(
                    Lane.GroundSamples.Last().DistanceAlongLaneCm,
                    Lane.LengthCm,
                    LengthToleranceCm) ||
                !FMath::IsNearlyEqual(
                    GeometryLengthCm,
                    Lane.LengthCm,
                    LengthToleranceCm))
            {
                return RejectCache(FString::Printf(
                    TEXT("lane_length_mismatch lane=%s declared=%.3f samples=%.3f geometry=%.3f"),
                    *Lane.LaneId.ToString(),
                    Lane.LengthCm,
                    Lane.GroundSamples.Last().DistanceAlongLaneCm,
                    GeometryLengthCm));
            }

            LanesById.Add(Lane.LaneId, &Lane);
            CertifiedLanes.Add(&Lane);
            CellGroundSampleCount += Lane.GroundSamples.Num();
            CellDirectionalLaneLengthCm += Lane.LengthCm;
        }

        const FOpenMassCrowdCentralCertificationEvidence& CellEvidence = Cell.Evidence;
        if (CellEvidence.CandidateLaneCount !=
                CellEvidence.CertifiedLaneCount + CellEvidence.RejectedLaneCount ||
            CellEvidence.CertifiedLaneCount != Cell.DirectedLanes.Num() ||
            CellEvidence.StrictGroundSampleCount != CellGroundSampleCount ||
            CellEvidence.ExactXYSupportPassCount != CellGroundSampleCount ||
            CellEvidence.FirstBlockerPassCount != CellGroundSampleCount ||
            CellEvidence.HeightContinuityPassCount != CellGroundSampleCount ||
            CellEvidence.SlopePassCount != CellGroundSampleCount ||
            CellEvidence.MultiTrackSupportPassCount != CellGroundSampleCount ||
            CellEvidence.CapsuleClearancePassCount != CellGroundSampleCount ||
            CellEvidence.CertifiedGeographicBlockCount != 1 ||
            CellEvidence.WholeAreaRecertificationCount != 0 ||
            !FMath::IsNearlyEqual(
                CellEvidence.CertifiedDirectionalLaneLengthCm,
                CellDirectionalLaneLengthCm,
                FMath::Max(2.0, CellDirectionalLaneLengthCm * 0.01)))
        {
            return RejectCache(FString::Printf(
                TEXT("inconsistent_cell_evidence cell=%s"),
                *Cell.CellId.ToString()));
        }

        for (const FOpenMassCrowdCentralPortal& Portal : Cell.Portals)
        {
            if (!Portal.bCertified ||
                !IsStrictCentralStableId(Portal.PortalId) ||
                !IsStrictCentralStableId(Portal.ReversePortalId) ||
                Portal.LocalCellId != Cell.CellId ||
                !IsStrictCentralStableId(Portal.RemoteCellId) ||
                Portal.RemoteCellId == Portal.LocalCellId ||
                !IsStrictCentralStableId(Portal.LocalNodeId) ||
                !IsStrictCentralStableId(Portal.RemoteNodeId) ||
                !IsStrictCentralStableId(Portal.DirectedLaneId) ||
                Portal.Position.ContainsNaN() ||
                PortalsById.Contains(Portal.PortalId))
            {
                return RejectCache(FString::Printf(
                    TEXT("invalid_or_uncertified_portal cell=%s portal=%s"),
                    *Cell.CellId.ToString(),
                    *Portal.PortalId.ToString()));
            }
            PortalsById.Add(Portal.PortalId, &Portal);
        }

        TotalGroundSampleCount += CellGroundSampleCount;
        TotalDirectionalLaneLengthCm += CellDirectionalLaneLengthCm;
        TotalPortalCount += Cell.Portals.Num();
    }

    if (CertifiedLanes.Num() < 2 || NodesById.Num() < 2)
    {
        return RejectCache(TEXT("network_too_small"));
    }

    const FOpenMassCrowdCentralCertificationEvidence& NetworkEvidence = Asset.Evidence;
    if (NetworkEvidence.CandidateLaneCount !=
            NetworkEvidence.CertifiedLaneCount + NetworkEvidence.RejectedLaneCount ||
        NetworkEvidence.CertifiedLaneCount != CertifiedLanes.Num() ||
        NetworkEvidence.StrictGroundSampleCount != TotalGroundSampleCount ||
        NetworkEvidence.ExactXYSupportPassCount != TotalGroundSampleCount ||
        NetworkEvidence.FirstBlockerPassCount != TotalGroundSampleCount ||
        NetworkEvidence.HeightContinuityPassCount != TotalGroundSampleCount ||
        NetworkEvidence.SlopePassCount != TotalGroundSampleCount ||
        NetworkEvidence.MultiTrackSupportPassCount != TotalGroundSampleCount ||
        NetworkEvidence.CapsuleClearancePassCount != TotalGroundSampleCount ||
        NetworkEvidence.ConnectedComponentCount != Asset.Components.Num() ||
        NetworkEvidence.CertifiedGeographicBlockCount != CellsById.Num() ||
        NetworkEvidence.CertifiedGeographicBlockCount < 4 ||
        NetworkEvidence.PortalCount != TotalPortalCount ||
        NetworkEvidence.SpawnDistrictCount != Asset.SpawnDistricts.Num() ||
        !FMath::IsNearlyEqual(
            NetworkEvidence.CertifiedDirectionalLaneLengthCm,
            TotalDirectionalLaneLengthCm,
            FMath::Max(2.0, TotalDirectionalLaneLengthCm * 0.01)))
    {
        return RejectCache(TEXT("inconsistent_network_evidence"));
    }

    CertifiedLanes.Sort(
        [](const FOpenMassCrowdCentralDirectedLane& A,
           const FOpenMassCrowdCentralDirectedLane& B)
        {
            return A.LaneId.LexicalLess(B.LaneId);
        });

    TMap<FName, int32> LaneIndicesById;
    TMap<FName, TArray<int32>> OutgoingLaneIndicesByNode;
    TMap<FName, TArray<int32>> IncomingLaneIndicesByNode;
    for (int32 LaneIndex = 0; LaneIndex < CertifiedLanes.Num(); ++LaneIndex)
    {
        const FOpenMassCrowdCentralDirectedLane& Lane = *CertifiedLanes[LaneIndex];
        LaneIndicesById.Add(Lane.LaneId, LaneIndex);
        OutgoingLaneIndicesByNode.FindOrAdd(Lane.FromNodeId).Add(LaneIndex);
        IncomingLaneIndicesByNode.FindOrAdd(Lane.ToNodeId).Add(LaneIndex);
    }

    TSet<FName> ReferencedNodeIds;
    for (const FOpenMassCrowdCentralDirectedLane* Lane : CertifiedLanes)
    {
        const FOpenMassCrowdCentralNode* const* FromNode = NodesById.Find(Lane->FromNodeId);
        const FOpenMassCrowdCentralNode* const* ToNode = NodesById.Find(Lane->ToNodeId);
        const FOpenMassCrowdCentralDirectedLane* const* ReverseLane =
            LanesById.Find(Lane->ReverseLaneId);
        if (!FromNode || !ToNode || !ReverseLane ||
            (*FromNode)->ComponentId != Lane->ComponentId ||
            (*ToNode)->ComponentId != Lane->ComponentId ||
            (*ReverseLane)->ComponentId != Lane->ComponentId ||
            (*ReverseLane)->FromNodeId != Lane->ToNodeId ||
            (*ReverseLane)->ToNodeId != Lane->FromNodeId ||
            (*ReverseLane)->ReverseLaneId != Lane->LaneId ||
            FVector::Distance(
                Lane->GroundSamples[0].CenterPosition,
                (*FromNode)->Position) > 5.0f ||
            FVector::Distance(
                Lane->GroundSamples.Last().CenterPosition,
                (*ToNode)->Position) > 5.0f)
        {
            return RejectCache(FString::Printf(
                TEXT("unknown_or_inconsistent_lane_reference lane=%s"),
                *Lane->LaneId.ToString()));
        }
        ReferencedNodeIds.Add(Lane->FromNodeId);
        ReferencedNodeIds.Add(Lane->ToNodeId);
    }
    if (ReferencedNodeIds.Num() != NodesById.Num())
    {
        return RejectCache(TEXT("orphan_nodes_present"));
    }

    const auto DeclaredLinksMatch =
        [&CertifiedLanes](
            const TArray<FName>& DeclaredLaneIds,
            const TArray<int32>* DerivedLaneIndices)
        {
            const int32 DerivedCount = DerivedLaneIndices ? DerivedLaneIndices->Num() : 0;
            if (DeclaredLaneIds.Num() != DerivedCount)
            {
                return false;
            }

            TSet<FName> DeclaredSet;
            for (const FName LaneId : DeclaredLaneIds)
            {
                if (!IsStrictCentralStableId(LaneId) || DeclaredSet.Contains(LaneId))
                {
                    return false;
                }
                DeclaredSet.Add(LaneId);
            }
            if (DerivedLaneIndices)
            {
                for (const int32 LaneIndex : *DerivedLaneIndices)
                {
                    if (!CertifiedLanes.IsValidIndex(LaneIndex) ||
                        !DeclaredSet.Contains(CertifiedLanes[LaneIndex]->LaneId))
                    {
                        return false;
                    }
                }
            }
            return true;
        };

    for (const TPair<FName, const FOpenMassCrowdCentralNode*>& NodePair : NodesById)
    {
        const FOpenMassCrowdCentralNode& Node = *NodePair.Value;
        if (!DeclaredLinksMatch(
                Node.OutgoingLaneIds,
                OutgoingLaneIndicesByNode.Find(Node.NodeId)) ||
            !DeclaredLinksMatch(
                Node.IncomingLaneIds,
                IncomingLaneIndicesByNode.Find(Node.NodeId)))
        {
            return RejectCache(FString::Printf(
                TEXT("node_adjacency_mismatch node=%s"),
                *Node.NodeId.ToString()));
        }
    }

    const auto CountReachableNodes =
        [&CertifiedLanes, &OutgoingLaneIndicesByNode, &IncomingLaneIndicesByNode](
            const FName StartNodeId,
            const bool bReverse)
        {
            TSet<FName> Visited;
            TArray<FName> Pending;
            Visited.Add(StartNodeId);
            Pending.Add(StartNodeId);
            for (int32 PendingIndex = 0; PendingIndex < Pending.Num(); ++PendingIndex)
            {
                const FName NodeId = Pending[PendingIndex];
                const TArray<int32>* LaneIndices = bReverse
                    ? IncomingLaneIndicesByNode.Find(NodeId)
                    : OutgoingLaneIndicesByNode.Find(NodeId);
                if (!LaneIndices)
                {
                    continue;
                }
                for (const int32 LaneIndex : *LaneIndices)
                {
                    const FOpenMassCrowdCentralDirectedLane& Lane =
                        *CertifiedLanes[LaneIndex];
                    const FName NextNodeId = bReverse
                        ? Lane.FromNodeId
                        : Lane.ToNodeId;
                    if (!Visited.Contains(NextNodeId))
                    {
                        Visited.Add(NextNodeId);
                        Pending.Add(NextNodeId);
                    }
                }
            }
            return Visited.Num();
        };

    TSet<FName> DeclaredComponentNodeIds;
    TSet<FName> DeclaredComponentLaneIds;
    for (const TPair<FName, const FOpenMassCrowdCentralComponent*>& ComponentPair :
         ComponentsById)
    {
        const FOpenMassCrowdCentralComponent& Component = *ComponentPair.Value;
        TSet<FName> ComponentCellIds;
        TSet<FName> ComponentNodeIds;
        TSet<FName> ComponentLaneIds;
        TSet<FName> DerivedCellIds;
        double DerivedLengthCm = 0.0;
        int32 DerivedJunctionCount = 0;

        for (const FName CellId : Component.CellIds)
        {
            if (!CellsById.Contains(CellId) || ComponentCellIds.Contains(CellId))
            {
                return RejectCache(FString::Printf(
                    TEXT("invalid_component_cell component=%s cell=%s"),
                    *Component.ComponentId.ToString(),
                    *CellId.ToString()));
            }
            ComponentCellIds.Add(CellId);
        }
        for (const FName NodeId : Component.NodeIds)
        {
            const FOpenMassCrowdCentralNode* const* Node = NodesById.Find(NodeId);
            if (!Node || (*Node)->ComponentId != Component.ComponentId ||
                ComponentNodeIds.Contains(NodeId) ||
                DeclaredComponentNodeIds.Contains(NodeId))
            {
                return RejectCache(FString::Printf(
                    TEXT("invalid_component_node component=%s node=%s"),
                    *Component.ComponentId.ToString(),
                    *NodeId.ToString()));
            }
            ComponentNodeIds.Add(NodeId);
            DeclaredComponentNodeIds.Add(NodeId);
            DerivedCellIds.Add((*Node)->CellId);
            DerivedJunctionCount +=
                (*Node)->Kind == EOpenMassCrowdCentralNodeKind::Junction ? 1 : 0;
        }
        for (const FName LaneId : Component.DirectedLaneIds)
        {
            const FOpenMassCrowdCentralDirectedLane* const* Lane = LanesById.Find(LaneId);
            if (!Lane || (*Lane)->ComponentId != Component.ComponentId ||
                !ComponentNodeIds.Contains((*Lane)->FromNodeId) ||
                !ComponentNodeIds.Contains((*Lane)->ToNodeId) ||
                ComponentLaneIds.Contains(LaneId) ||
                DeclaredComponentLaneIds.Contains(LaneId))
            {
                return RejectCache(FString::Printf(
                    TEXT("invalid_component_lane component=%s lane=%s"),
                    *Component.ComponentId.ToString(),
                    *LaneId.ToString()));
            }
            ComponentLaneIds.Add(LaneId);
            DeclaredComponentLaneIds.Add(LaneId);
            DerivedLengthCm += (*Lane)->LengthCm;
        }
        if (DerivedCellIds.Num() != ComponentCellIds.Num())
        {
            return RejectCache(TEXT("component_cell_partition_mismatch"));
        }
        for (const FName CellId : DerivedCellIds)
        {
            if (!ComponentCellIds.Contains(CellId))
            {
                return RejectCache(TEXT("component_cell_partition_mismatch"));
            }
        }
        const int32 DerivedStreetBlockCount = FMath::Max(
            0,
            ComponentLaneIds.Num() / 2 - ComponentNodeIds.Num() + 1);
        if (DerivedJunctionCount != Component.JunctionCount ||
            DerivedStreetBlockCount != Component.StreetBlockCount ||
            !FMath::IsNearlyEqual(
                DerivedLengthCm,
                Component.DirectionalLaneLengthCm,
                FMath::Max(2.0, DerivedLengthCm * 0.01)))
        {
            return RejectCache(FString::Printf(
                TEXT("component_evidence_mismatch component=%s"),
                *Component.ComponentId.ToString()));
        }

        const FName ConnectivityRoot = Component.NodeIds[0];
        const int32 ForwardReachableNodeCount =
            CountReachableNodes(ConnectivityRoot, false);
        const int32 ReverseReachableNodeCount =
            CountReachableNodes(ConnectivityRoot, true);
        if (ForwardReachableNodeCount != ComponentNodeIds.Num() ||
            ReverseReachableNodeCount != ComponentNodeIds.Num())
        {
            return RejectCache(FString::Printf(
                TEXT("component_not_strongly_connected component=%s forward=%d reverse=%d nodes=%d"),
                *Component.ComponentId.ToString(),
                ForwardReachableNodeCount,
                ReverseReachableNodeCount,
                ComponentNodeIds.Num()));
        }
    }
    if (DeclaredComponentNodeIds.Num() != NodesById.Num() ||
        DeclaredComponentLaneIds.Num() != LanesById.Num())
    {
        return RejectCache(TEXT("component_records_do_not_partition_graph"));
    }

    TSet<FName> PortalLaneIds;
    for (const TPair<FName, const FOpenMassCrowdCentralPortal*>& PortalPair : PortalsById)
    {
        const FOpenMassCrowdCentralPortal& Portal = *PortalPair.Value;
        const FOpenMassCrowdCentralPortal* const* ReversePortal =
            PortalsById.Find(Portal.ReversePortalId);
        const FOpenMassCrowdCentralCell* const* LocalCell =
            CellsById.Find(Portal.LocalCellId);
        const FOpenMassCrowdCentralCell* const* RemoteCell =
            CellsById.Find(Portal.RemoteCellId);
        const FOpenMassCrowdCentralNode* const* LocalNode =
            NodesById.Find(Portal.LocalNodeId);
        const FOpenMassCrowdCentralNode* const* RemoteNode =
            NodesById.Find(Portal.RemoteNodeId);
        const FOpenMassCrowdCentralDirectedLane* const* PortalLane =
            LanesById.Find(Portal.DirectedLaneId);
        if (!ReversePortal || !LocalCell || !RemoteCell || !LocalNode || !RemoteNode ||
            !PortalLane ||
            (*ReversePortal)->ReversePortalId != Portal.PortalId ||
            (*ReversePortal)->LocalCellId != Portal.RemoteCellId ||
            (*ReversePortal)->RemoteCellId != Portal.LocalCellId ||
            (*ReversePortal)->LocalNodeId != Portal.RemoteNodeId ||
            (*ReversePortal)->RemoteNodeId != Portal.LocalNodeId ||
            (*LocalNode)->CellId != Portal.LocalCellId ||
            (*RemoteNode)->CellId != Portal.RemoteCellId ||
            (*LocalNode)->ComponentId != (*PortalLane)->ComponentId ||
            (*RemoteNode)->ComponentId != (*PortalLane)->ComponentId ||
            (*PortalLane)->CellId != Portal.LocalCellId ||
            (*PortalLane)->FromNodeId != Portal.LocalNodeId ||
            (*PortalLane)->ToNodeId != Portal.RemoteNodeId)
        {
            return RejectCache(FString::Printf(
                TEXT("unknown_or_inconsistent_portal_reference portal=%s"),
                *Portal.PortalId.ToString()));
        }
        PortalLaneIds.Add(Portal.DirectedLaneId);
    }
    for (const FOpenMassCrowdCentralDirectedLane* Lane : CertifiedLanes)
    {
        const FOpenMassCrowdCentralNode& FromNode = *NodesById[Lane->FromNodeId];
        const FOpenMassCrowdCentralNode& ToNode = *NodesById[Lane->ToNodeId];
        const bool bCrossesCell = FromNode.CellId != ToNode.CellId;
        if (bCrossesCell != PortalLaneIds.Contains(Lane->LaneId))
        {
            return RejectCache(FString::Printf(
                TEXT("portal_lane_coverage_mismatch lane=%s"),
                *Lane->LaneId.ToString()));
        }
    }

    if (Asset.SpawnDistricts.Num() != RequiredCentralSpawnDistrictCount ||
        Asset.GetConfiguredPopulation() != FullCentralPopulation)
    {
        return RejectCache(FString::Printf(
            TEXT("spawn_district_balance_invalid districts=%d population=%d"),
            Asset.SpawnDistricts.Num(),
            Asset.GetConfiguredPopulation()));
    }
    TSet<FName> DistrictIds;
    for (int32 DistrictIndex = 0;
         DistrictIndex < Asset.SpawnDistricts.Num();
         ++DistrictIndex)
    {
        const FOpenMassCrowdCentralSpawnDistrict& District =
            Asset.SpawnDistricts[DistrictIndex];
        if (!District.bEnabled ||
            !IsStrictCentralStableId(District.DistrictId) ||
            !ComponentsById.Contains(District.ComponentId) ||
            DistrictIds.Contains(District.DistrictId) ||
            District.CellIds.IsEmpty() ||
            District.SpawnNodeIds.IsEmpty() ||
            District.SpawnLaneIds.IsEmpty() ||
            !IsFiniteCentralBox(District.WorldBounds) ||
            District.TargetPopulation !=
                GetCentralFullDistrictPopulation(DistrictIndex) ||
            !FMath::IsFinite(District.SelectionWeight) ||
            District.SelectionWeight <= 0.0f)
        {
            return RejectCache(FString::Printf(
                TEXT("invalid_spawn_district district=%s"),
                *District.DistrictId.ToString()));
        }
        DistrictIds.Add(District.DistrictId);

        TSet<FName> DistrictCellIds;
        for (const FName CellId : District.CellIds)
        {
            if (!IsStrictCentralStableId(CellId) ||
                DistrictCellIds.Contains(CellId) ||
                !CellsById.Contains(CellId))
            {
                return RejectCache(FString::Printf(
                    TEXT("unknown_district_cell district=%s cell=%s"),
                    *District.DistrictId.ToString(),
                    *CellId.ToString()));
            }
            DistrictCellIds.Add(CellId);
        }

        TSet<FName> DistrictSpawnNodeIds;
        for (const FName NodeId : District.SpawnNodeIds)
        {
            const FOpenMassCrowdCentralNode* const* Node = NodesById.Find(NodeId);
            if (!Node ||
                (*Node)->ComponentId != District.ComponentId ||
                DistrictSpawnNodeIds.Contains(NodeId) ||
                !DistrictCellIds.Contains((*Node)->CellId))
            {
                return RejectCache(FString::Printf(
                    TEXT("unknown_district_spawn_node district=%s node=%s"),
                    *District.DistrictId.ToString(),
                    *NodeId.ToString()));
            }
            DistrictSpawnNodeIds.Add(NodeId);
        }

        TSet<FName> DistrictSpawnLaneIds;
        for (const FName LaneId : District.SpawnLaneIds)
        {
            const FOpenMassCrowdCentralDirectedLane* const* Lane = LanesById.Find(LaneId);
            if (!Lane ||
                (*Lane)->ComponentId != District.ComponentId ||
                DistrictSpawnLaneIds.Contains(LaneId) ||
                !DistrictCellIds.Contains((*Lane)->CellId))
            {
                return RejectCache(FString::Printf(
                    TEXT("unknown_district_spawn_lane district=%s lane=%s"),
                    *District.DistrictId.ToString(),
                    *LaneId.ToString()));
            }
            DistrictSpawnLaneIds.Add(LaneId);
        }
    }

    // Several OSM semantic-recovery candidates can describe the same physical
    // pavement with points only a few centimetres apart (for example the
    // m020/p020/z000 variants observed at runtime).  Build a deterministic,
    // direction-specific equivalence relation from the complete ordered right
    // support tracks.  This is not topology rewriting: every lane and route
    // remains intact, while spacing treats physically inseparable copies as
    // one occupancy resource.
    const auto InterpolateRightTrack = [](
        const FOpenMassCrowdCentralDirectedLane& Lane,
        const double DistanceAlongLaneCm)
    {
        const TArray<FOpenMassCrowdCentralGroundSample>& Samples =
            Lane.GroundSamples;
        if (DistanceAlongLaneCm <= Samples[0].DistanceAlongLaneCm)
        {
            return Samples[0].RightTrackPosition;
        }
        if (DistanceAlongLaneCm >= Samples.Last().DistanceAlongLaneCm)
        {
            return Samples.Last().RightTrackPosition;
        }

        int32 LowerIndex = 0;
        int32 UpperIndex = Samples.Num() - 1;
        while (LowerIndex + 1 < UpperIndex)
        {
            const int32 MiddleIndex = (LowerIndex + UpperIndex) / 2;
            if (Samples[MiddleIndex].DistanceAlongLaneCm <=
                DistanceAlongLaneCm)
            {
                LowerIndex = MiddleIndex;
            }
            else
            {
                UpperIndex = MiddleIndex;
            }
        }
        const double SegmentLength =
            Samples[UpperIndex].DistanceAlongLaneCm -
            Samples[LowerIndex].DistanceAlongLaneCm;
        const double Alpha = SegmentLength > UE_DOUBLE_SMALL_NUMBER
            ? (DistanceAlongLaneCm -
               Samples[LowerIndex].DistanceAlongLaneCm) /
                  SegmentLength
            : 0.0;
        return FMath::Lerp(
            Samples[LowerIndex].RightTrackPosition,
            Samples[UpperIndex].RightTrackPosition,
            Alpha);
    };
    const auto AreCentralTracksPhysicallyEquivalent =
        [&InterpolateRightTrack](
            const FOpenMassCrowdCentralDirectedLane& A,
            const FOpenMassCrowdCentralDirectedLane& B)
    {
        const FVector ADirection =
            (InterpolateRightTrack(A, A.LengthCm) -
             InterpolateRightTrack(A, 0.0)).GetSafeNormal();
        const FVector BDirection =
            (InterpolateRightTrack(B, B.LengthCm) -
             InterpolateRightTrack(B, 0.0)).GetSafeNormal();
        if (A.ComponentId != B.ComponentId ||
            A.ReverseLaneId == B.LaneId ||
            B.ReverseLaneId == A.LaneId ||
            FVector::DotProduct(ADirection, BDirection) <= 0.5 ||
            FMath::Abs(A.LengthCm - B.LengthCm) >=
                CentralMinimumCenterClearanceCm ||
            FVector::Distance(
                InterpolateRightTrack(A, 0.0),
                InterpolateRightTrack(B, 0.0)) >=
                CentralMinimumCenterClearanceCm ||
            FVector::Distance(
                InterpolateRightTrack(A, A.LengthCm),
                InterpolateRightTrack(B, B.LengthCm)) >=
                CentralMinimumCenterClearanceCm)
        {
            return false;
        }

        const auto AllSamplesMatch = [&InterpolateRightTrack](
            const FOpenMassCrowdCentralDirectedLane& Source,
            const FOpenMassCrowdCentralDirectedLane& Target)
        {
            for (const FOpenMassCrowdCentralGroundSample& Sample :
                 Source.GroundSamples)
            {
                const double NormalizedDistance = Source.LengthCm >
                        UE_DOUBLE_SMALL_NUMBER
                    ? Sample.DistanceAlongLaneCm / Source.LengthCm
                    : 0.0;
                const FVector TargetPoint = InterpolateRightTrack(
                    Target,
                    FMath::Clamp(NormalizedDistance, 0.0, 1.0) *
                        Target.LengthCm);
                if (FVector::Distance(
                        Sample.RightTrackPosition,
                        TargetPoint) >=
                    CentralMinimumCenterClearanceCm)
                {
                    return false;
                }
            }
            return true;
        };
        return AllSamplesMatch(A, B) && AllSamplesMatch(B, A);
    };
    const auto MeasureCentralOpposingMaximumSeparationCm =
        [&InterpolateRightTrack](
            const FOpenMassCrowdCentralDirectedLane& A,
            const FOpenMassCrowdCentralDirectedLane& B)
    {
        double MaximumSeparationCm = 0.0;
        const auto MeasureOpposingSamples =
            [&InterpolateRightTrack, &MaximumSeparationCm](
                const FOpenMassCrowdCentralDirectedLane& Source,
                const FOpenMassCrowdCentralDirectedLane& Target)
        {
            for (const FOpenMassCrowdCentralGroundSample& Sample :
                 Source.GroundSamples)
            {
                const double NormalizedDistance = Source.LengthCm >
                        UE_DOUBLE_SMALL_NUMBER
                    ? Sample.DistanceAlongLaneCm / Source.LengthCm
                    : 0.0;
                const FVector TargetPoint = InterpolateRightTrack(
                    Target,
                    (1.0 - FMath::Clamp(
                        NormalizedDistance,
                        0.0,
                        1.0)) * Target.LengthCm);
                MaximumSeparationCm = FMath::Max(
                    MaximumSeparationCm,
                    FVector::Distance(
                        Sample.RightTrackPosition,
                        TargetPoint));
            }
        };
        MeasureOpposingSamples(A, B);
        MeasureOpposingSamples(B, A);
        return MaximumSeparationCm;
    };
    const auto AreCentralTracksOpposingPhysicalConflict =
        [&InterpolateRightTrack,
         &MeasureCentralOpposingMaximumSeparationCm](
            const FOpenMassCrowdCentralDirectedLane& A,
            const FOpenMassCrowdCentralDirectedLane& B)
    {
        const FVector ADirection =
            (InterpolateRightTrack(A, A.LengthCm) -
             InterpolateRightTrack(A, 0.0)).GetSafeNormal();
        const FVector BDirection =
            (InterpolateRightTrack(B, B.LengthCm) -
             InterpolateRightTrack(B, 0.0)).GetSafeNormal();
        if (A.ComponentId != B.ComponentId ||
            FVector::DotProduct(ADirection, BDirection) >= -0.5 ||
            FMath::Abs(A.LengthCm - B.LengthCm) >=
                CentralOpposingCorridorToleranceCm ||
            FVector::Distance(
                InterpolateRightTrack(A, 0.0),
                InterpolateRightTrack(B, B.LengthCm)) >=
                CentralOpposingCorridorToleranceCm ||
            FVector::Distance(
                InterpolateRightTrack(A, A.LengthCm),
                InterpolateRightTrack(B, 0.0)) >=
                CentralOpposingCorridorToleranceCm)
        {
            return false;
        }
        return MeasureCentralOpposingMaximumSeparationCm(A, B) <
            CentralOpposingCorridorToleranceCm;
    };

    TArray<int32> PhysicalTrackParents;
    PhysicalTrackParents.SetNumUninitialized(CertifiedLanes.Num());
    for (int32 LaneIndex = 0;
         LaneIndex < PhysicalTrackParents.Num();
         ++LaneIndex)
    {
        PhysicalTrackParents[LaneIndex] = LaneIndex;
    }
    TArray<TArray<int32>> SameDirectionPhysicalConflicts;
    TArray<TArray<int32>> OpposingPhysicalConflicts;
    SameDirectionPhysicalConflicts.SetNum(CertifiedLanes.Num());
    OpposingPhysicalConflicts.SetNum(CertifiedLanes.Num());
    int32 DeclaredReversePairCount = 0;
    int32 DeclaredReverseIncludedPairCount = 0;
    int32 DeclaredReverseExcludedGeometryPairCount = 0;
    double DeclaredReverseIncludedMaximumSeparationCm = 0.0;
    double DeclaredReverseExcludedMinimumSeparationCm =
        TNumericLimits<double>::Max();
    const auto FindPhysicalTrackRoot = [&PhysicalTrackParents](
        const int32 LaneIndex)
    {
        int32 Root = LaneIndex;
        while (PhysicalTrackParents[Root] != Root)
        {
            Root = PhysicalTrackParents[Root];
        }
        int32 Current = LaneIndex;
        while (PhysicalTrackParents[Current] != Current)
        {
            const int32 Parent = PhysicalTrackParents[Current];
            PhysicalTrackParents[Current] = Root;
            Current = Parent;
        }
        return Root;
    };
    for (int32 FirstLaneIndex = 0;
         FirstLaneIndex < CertifiedLanes.Num();
         ++FirstLaneIndex)
    {
        for (int32 SecondLaneIndex = FirstLaneIndex + 1;
             SecondLaneIndex < CertifiedLanes.Num();
             ++SecondLaneIndex)
        {
            const FOpenMassCrowdCentralDirectedLane& FirstLane =
                *CertifiedLanes[FirstLaneIndex];
            const FOpenMassCrowdCentralDirectedLane& SecondLane =
                *CertifiedLanes[SecondLaneIndex];
            const bool bDeclaredReversePair =
                FirstLane.ReverseLaneId == SecondLane.LaneId &&
                SecondLane.ReverseLaneId == FirstLane.LaneId;
            double DeclaredReverseMaximumSeparationCm = 0.0;
            if (bDeclaredReversePair)
            {
                ++DeclaredReversePairCount;
                DeclaredReverseMaximumSeparationCm =
                    MeasureCentralOpposingMaximumSeparationCm(
                        FirstLane,
                        SecondLane);
            }
            if (AreCentralTracksPhysicallyEquivalent(
                    FirstLane,
                    SecondLane))
            {
                SameDirectionPhysicalConflicts[FirstLaneIndex].Add(
                    SecondLaneIndex);
                SameDirectionPhysicalConflicts[SecondLaneIndex].Add(
                    FirstLaneIndex);
                const int32 FirstRoot =
                    FindPhysicalTrackRoot(FirstLaneIndex);
                const int32 SecondRoot =
                    FindPhysicalTrackRoot(SecondLaneIndex);
                if (FirstRoot != SecondRoot)
                {
                    const int32 CanonicalRoot =
                        FMath::Min(FirstRoot, SecondRoot);
                    PhysicalTrackParents[
                        FMath::Max(FirstRoot, SecondRoot)] =
                            CanonicalRoot;
                }
                continue;
            }
            if (AreCentralTracksOpposingPhysicalConflict(
                    FirstLane,
                    SecondLane))
            {
                OpposingPhysicalConflicts[FirstLaneIndex].Add(
                    SecondLaneIndex);
                OpposingPhysicalConflicts[SecondLaneIndex].Add(
                    FirstLaneIndex);
                if (bDeclaredReversePair)
                {
                    ++DeclaredReverseIncludedPairCount;
                    DeclaredReverseIncludedMaximumSeparationCm = FMath::Max(
                        DeclaredReverseIncludedMaximumSeparationCm,
                        DeclaredReverseMaximumSeparationCm);
                }
            }
            else if (bDeclaredReversePair)
            {
                ++DeclaredReverseExcludedGeometryPairCount;
                DeclaredReverseExcludedMinimumSeparationCm = FMath::Min(
                    DeclaredReverseExcludedMinimumSeparationCm,
                    DeclaredReverseMaximumSeparationCm);
            }
        }
    }

    // Whole-track synchronization is insufficient for real streets: two
    // certified polylines can merge, cross, or touch for only a few samples,
    // including pairs with no shared topology node. Build the complete direct
    // local conflict set from the immutable right-track samples. The guard
    // radius adds the maximum observed certified segment length; by the nearest
    // endpoint bound it cannot miss a continuous segment pair whose real
    // centerlines come within the 20 cm hard-conflict distance between samples.
    float MaximumCertifiedSegmentLengthCm = 0.0f;
    for (const FOpenMassCrowdCentralDirectedLane* Lane : CertifiedLanes)
    {
        for (int32 SampleIndex = 1;
             SampleIndex < Lane->GroundSamples.Num();
             ++SampleIndex)
        {
            MaximumCertifiedSegmentLengthCm = FMath::Max(
                MaximumCertifiedSegmentLengthCm,
                FVector::Distance(
                    Lane->GroundSamples[SampleIndex - 1].RightTrackPosition,
                    Lane->GroundSamples[SampleIndex].RightTrackPosition));
        }
    }
    const float LocalConflictDetectionRadiusCm =
        CentralSevereOverlapDistanceCm +
        MaximumCertifiedSegmentLengthCm + 0.01f;
    const float LocalConflictDetectionRadiusSquared =
        FMath::Square(LocalConflictDetectionRadiusCm);
    const float StrictLocalConflictRadiusSquared =
        FMath::Square(CentralSevereOverlapDistanceCm);

    struct FCentralConflictSampleRef
    {
        int32 LaneIndex = INDEX_NONE;
        float DistanceAlongLaneCm = 0.0f;
        FVector Position = FVector::ZeroVector;
    };
    struct FCentralLocalConflictBuilder
    {
        int32 FirstLaneIndex = INDEX_NONE;
        int32 SecondLaneIndex = INDEX_NONE;
        float FirstMinimumDistanceCm = TNumericLimits<float>::Max();
        float FirstMaximumDistanceCm = 0.0f;
        float SecondMinimumDistanceCm = TNumericLimits<float>::Max();
        float SecondMaximumDistanceCm = 0.0f;
        TArray<FVector2f> StrictSampleDistancesCm;
        bool bContainsStrictSampleConflict = false;
    };
    TMap<FIntVector, TArray<FCentralConflictSampleRef>> ConflictSampleBuckets;
    TMap<uint64, FCentralLocalConflictBuilder> LocalConflictBuilders;
    for (int32 LaneIndex = 0;
         LaneIndex < CertifiedLanes.Num();
         ++LaneIndex)
    {
        for (const FOpenMassCrowdCentralGroundSample& Sample :
             CertifiedLanes[LaneIndex]->GroundSamples)
        {
            const FVector& Position = Sample.RightTrackPosition;
            const FIntVector Bucket(
                FMath::FloorToInt(Position.X /
                    LocalConflictDetectionRadiusCm),
                FMath::FloorToInt(Position.Y /
                    LocalConflictDetectionRadiusCm),
                FMath::FloorToInt(Position.Z /
                    LocalConflictDetectionRadiusCm));
            for (int32 DeltaX = -1; DeltaX <= 1; ++DeltaX)
            {
                for (int32 DeltaY = -1; DeltaY <= 1; ++DeltaY)
                {
                    for (int32 DeltaZ = -1; DeltaZ <= 1; ++DeltaZ)
                    {
                        const TArray<FCentralConflictSampleRef>* NearbySamples =
                            ConflictSampleBuckets.Find(
                                Bucket + FIntVector(
                                    DeltaX,
                                    DeltaY,
                                    DeltaZ));
                        if (!NearbySamples)
                        {
                            continue;
                        }
                        for (const FCentralConflictSampleRef& OtherSample :
                             *NearbySamples)
                        {
                            if (OtherSample.LaneIndex == LaneIndex)
                            {
                                continue;
                            }
                            const float SquaredDistance = FVector::DistSquared(
                                Position,
                                OtherSample.Position);
                            if (SquaredDistance >=
                                LocalConflictDetectionRadiusSquared)
                            {
                                continue;
                            }

                            const int32 FirstLaneIndex = FMath::Min(
                                LaneIndex,
                                OtherSample.LaneIndex);
                            const int32 SecondLaneIndex = FMath::Max(
                                LaneIndex,
                                OtherSample.LaneIndex);
                            const uint64 PairKey =
                                (static_cast<uint64>(
                                    static_cast<uint32>(FirstLaneIndex)) << 32) |
                                static_cast<uint32>(SecondLaneIndex);
                            FCentralLocalConflictBuilder& Builder =
                                LocalConflictBuilders.FindOrAdd(PairKey);
                            if (Builder.FirstLaneIndex == INDEX_NONE)
                            {
                                Builder.FirstLaneIndex = FirstLaneIndex;
                                Builder.SecondLaneIndex = SecondLaneIndex;
                            }
                            const float FirstDistanceCm =
                                LaneIndex == FirstLaneIndex
                                ? static_cast<float>(
                                    Sample.DistanceAlongLaneCm)
                                : OtherSample.DistanceAlongLaneCm;
                            const float SecondDistanceCm =
                                LaneIndex == SecondLaneIndex
                                ? static_cast<float>(
                                    Sample.DistanceAlongLaneCm)
                                : OtherSample.DistanceAlongLaneCm;
                            Builder.FirstMinimumDistanceCm = FMath::Min(
                                Builder.FirstMinimumDistanceCm,
                                FirstDistanceCm);
                            Builder.FirstMaximumDistanceCm = FMath::Max(
                                Builder.FirstMaximumDistanceCm,
                                FirstDistanceCm);
                            Builder.SecondMinimumDistanceCm = FMath::Min(
                                Builder.SecondMinimumDistanceCm,
                                SecondDistanceCm);
                            Builder.SecondMaximumDistanceCm = FMath::Max(
                                Builder.SecondMaximumDistanceCm,
                                SecondDistanceCm);
                            const bool bStrictSampleConflict =
                                SquaredDistance < StrictLocalConflictRadiusSquared;
                            Builder.bContainsStrictSampleConflict |=
                                bStrictSampleConflict;
                            if (bStrictSampleConflict)
                            {
                                Builder.StrictSampleDistancesCm.Emplace(
                                    FirstDistanceCm,
                                    SecondDistanceCm);
                            }
                        }
                    }
                }
            }

            FCentralConflictSampleRef& AddedSample =
                ConflictSampleBuckets.FindOrAdd(Bucket).AddDefaulted_GetRef();
            AddedSample.LaneIndex = LaneIndex;
            AddedSample.DistanceAlongLaneCm = static_cast<float>(
                Sample.DistanceAlongLaneCm);
            AddedSample.Position = Position;
        }
    }

    TArray<FRuntimeCentralLocalConflict> LocalConflicts;
    TArray<TArray<int32>> LocalConflictIndicesByLane;
    TArray<TArray<int32>> CollisionConflictLaneIndices;
    LocalConflictIndicesByLane.SetNum(CertifiedLanes.Num());
    CollisionConflictLaneIndices.SetNum(CertifiedLanes.Num());
    const auto AddCollisionAdjacency = [&CollisionConflictLaneIndices](
        const int32 FirstLaneIndex,
        const int32 SecondLaneIndex)
    {
        CollisionConflictLaneIndices[FirstLaneIndex].AddUnique(
            SecondLaneIndex);
        CollisionConflictLaneIndices[SecondLaneIndex].AddUnique(
            FirstLaneIndex);
    };
    for (int32 LaneIndex = 0;
         LaneIndex < CertifiedLanes.Num();
         ++LaneIndex)
    {
        for (const int32 ConflictLaneIndex :
             SameDirectionPhysicalConflicts[LaneIndex])
        {
            if (LaneIndex < ConflictLaneIndex)
            {
                AddCollisionAdjacency(LaneIndex, ConflictLaneIndex);
            }
        }
        for (const int32 ConflictLaneIndex :
             OpposingPhysicalConflicts[LaneIndex])
        {
            if (LaneIndex < ConflictLaneIndex)
            {
                AddCollisionAdjacency(LaneIndex, ConflictLaneIndex);
            }
        }
    }

    int32 StrictLocalConflictPairCount = 0;
    int32 StrictWholeTrackCoveredPairCount = 0;
    int32 StrictIntervalCoveredPairCount = 0;
    int32 StrictUncoveredPairCount = 0;
    int32 StrictSameSharedNodePairCount = 0;
    int32 StrictSameNonSharedNodePairCount = 0;
    int32 StrictOpposingSharedNodePairCount = 0;
    int32 StrictOpposingNonSharedNodePairCount = 0;
    int32 StrictCrossSharedNodePairCount = 0;
    int32 StrictCrossNonSharedNodePairCount = 0;
    int32 StrictDeclaredReversePairCount = 0;
    int32 StrictWholeOpposingPairCount = 0;
    int32 RuntimeOnlyOpposingIntervalCount = 0;
    int32 RuntimeOnlyOpposingCoveredPairCount = 0;
    TArray<uint64> StableLocalConflictPairKeys;
    LocalConflictBuilders.GenerateKeyArray(
        StableLocalConflictPairKeys);
    StableLocalConflictPairKeys.Sort();
    for (const uint64 PairKey : StableLocalConflictPairKeys)
    {
        const FCentralLocalConflictBuilder& Builder =
            LocalConflictBuilders.FindChecked(PairKey);
        const FOpenMassCrowdCentralDirectedLane& FirstLane =
            *CertifiedLanes[Builder.FirstLaneIndex];
        const FOpenMassCrowdCentralDirectedLane& SecondLane =
            *CertifiedLanes[Builder.SecondLaneIndex];
        const bool bWholeTrackSame =
            SameDirectionPhysicalConflicts[Builder.FirstLaneIndex].Contains(
                Builder.SecondLaneIndex);
        const bool bWholeTrackOpposing =
            OpposingPhysicalConflicts[Builder.FirstLaneIndex].Contains(
                Builder.SecondLaneIndex);

        // A 55 cm whole-opposing classification remains authoritative for
        // spawn admission and occupancy capacity, but it is intentionally not
        // a runtime lane-long mutex. Only a pair with a certified sample below
        // the 20 cm hard threshold receives runtime-local resources.
        const bool bRuntimeOnlyOpposingInterval =
            bWholeTrackOpposing && Builder.bContainsStrictSampleConflict;
        const auto AddLocalConflictResource = [
            &LocalConflicts,
            &LocalConflictIndicesByLane,
            &AddCollisionAdjacency,
            &FirstLane,
            &SecondLane,
            &Builder,
            MaximumCertifiedSegmentLengthCm](
                const float FirstMinimumDistanceCm,
                const float FirstMaximumDistanceCm,
                const float SecondMinimumDistanceCm,
                const float SecondMaximumDistanceCm,
                const bool bRuntimeOnlyReservation)
        {
            FRuntimeCentralLocalConflict& Conflict =
                LocalConflicts.AddDefaulted_GetRef();
            Conflict.FirstLaneIndex = Builder.FirstLaneIndex;
            Conflict.SecondLaneIndex = Builder.SecondLaneIndex;
            Conflict.bRuntimeOnlyReservation = bRuntimeOnlyReservation;
            Conflict.FirstBeginDistanceCm = FMath::Clamp(
                FirstMinimumDistanceCm -
                    MaximumCertifiedSegmentLengthCm - 0.01f,
                0.0f,
                static_cast<float>(FirstLane.LengthCm));
            Conflict.FirstEndDistanceCm = FMath::Clamp(
                FirstMaximumDistanceCm +
                    MaximumCertifiedSegmentLengthCm + 0.01f,
                Conflict.FirstBeginDistanceCm,
                static_cast<float>(FirstLane.LengthCm));
            Conflict.SecondBeginDistanceCm = FMath::Clamp(
                SecondMinimumDistanceCm -
                    MaximumCertifiedSegmentLengthCm - 0.01f,
                0.0f,
                static_cast<float>(SecondLane.LengthCm));
            Conflict.SecondEndDistanceCm = FMath::Clamp(
                SecondMaximumDistanceCm +
                    MaximumCertifiedSegmentLengthCm + 0.01f,
                Conflict.SecondBeginDistanceCm,
                static_cast<float>(SecondLane.LengthCm));
            const int32 ConflictIndex = LocalConflicts.Num() - 1;
            LocalConflictIndicesByLane[Builder.FirstLaneIndex].Add(
                ConflictIndex);
            LocalConflictIndicesByLane[Builder.SecondLaneIndex].Add(
                ConflictIndex);
            AddCollisionAdjacency(
                Builder.FirstLaneIndex,
                Builder.SecondLaneIndex);
        };

        bool bIntervalCovered = false;
        if (bRuntimeOnlyOpposingInterval)
        {
            // Keep separated strict stations separated. Each sample resource
            // receives one certification-segment guard; runtime closures merge
            // only resources whose 20 cm movement guards actually overlap.
            TArray<FVector2f> StableStrictSamples =
                Builder.StrictSampleDistancesCm;
            StableStrictSamples.Sort(
                [](const FVector2f& A, const FVector2f& B)
                {
                    return A.X == B.X ? A.Y < B.Y : A.X < B.X;
                });
            for (const FVector2f& StrictSample : StableStrictSamples)
            {
                AddLocalConflictResource(
                    StrictSample.X,
                    StrictSample.X,
                    StrictSample.Y,
                    StrictSample.Y,
                    true);
            }
            bIntervalCovered = !StableStrictSamples.IsEmpty();
            RuntimeOnlyOpposingIntervalCount +=
                StableStrictSamples.Num();
            RuntimeOnlyOpposingCoveredPairCount +=
                bIntervalCovered ? 1 : 0;
        }
        else if (!bWholeTrackSame && !bWholeTrackOpposing)
        {
            AddLocalConflictResource(
                Builder.FirstMinimumDistanceCm,
                Builder.FirstMaximumDistanceCm,
                Builder.SecondMinimumDistanceCm,
                Builder.SecondMaximumDistanceCm,
                false);
            bIntervalCovered = true;
        }

        if (!Builder.bContainsStrictSampleConflict)
        {
            continue;
        }
        ++StrictLocalConflictPairCount;
        StrictWholeOpposingPairCount += bWholeTrackOpposing ? 1 : 0;
        if (bWholeTrackSame)
        {
            ++StrictWholeTrackCoveredPairCount;
        }
        else if (bIntervalCovered)
        {
            ++StrictIntervalCoveredPairCount;
        }
        else
        {
            ++StrictUncoveredPairCount;
        }

        const bool bDeclaredReversePair =
            FirstLane.ReverseLaneId == SecondLane.LaneId &&
            SecondLane.ReverseLaneId == FirstLane.LaneId;
        if (bDeclaredReversePair)
        {
            ++StrictDeclaredReversePairCount;
            continue;
        }
        const bool bSharedNode =
            FirstLane.FromNodeId == SecondLane.FromNodeId ||
            FirstLane.FromNodeId == SecondLane.ToNodeId ||
            FirstLane.ToNodeId == SecondLane.FromNodeId ||
            FirstLane.ToNodeId == SecondLane.ToNodeId;
        const FVector FirstDirection =
            (InterpolateRightTrack(FirstLane, FirstLane.LengthCm) -
             InterpolateRightTrack(FirstLane, 0.0)).GetSafeNormal();
        const FVector SecondDirection =
            (InterpolateRightTrack(SecondLane, SecondLane.LengthCm) -
             InterpolateRightTrack(SecondLane, 0.0)).GetSafeNormal();
        const float DirectionDot = FVector::DotProduct(
            FirstDirection,
            SecondDirection);
        if (DirectionDot > 0.5f)
        {
            (bSharedNode
                ? StrictSameSharedNodePairCount
                : StrictSameNonSharedNodePairCount)++;
        }
        else if (DirectionDot < -0.5f)
        {
            (bSharedNode
                ? StrictOpposingSharedNodePairCount
                : StrictOpposingNonSharedNodePairCount)++;
        }
        else
        {
            (bSharedNode
                ? StrictCrossSharedNodePairCount
                : StrictCrossNonSharedNodePairCount)++;
        }
    }
    if (RuntimeOnlyOpposingCoveredPairCount != StrictWholeOpposingPairCount)
    {
        return RejectCache(FString::Printf(
            TEXT("runtime_opposing_interval_coverage_incomplete strict_whole_opposing=%d covered_pairs=%d runtime_intervals=%d"),
            StrictWholeOpposingPairCount,
            RuntimeOnlyOpposingCoveredPairCount,
            RuntimeOnlyOpposingIntervalCount));
    }
    for (TArray<int32>& LaneIndices : CollisionConflictLaneIndices)
    {
        LaneIndices.Sort();
    }

    // Audit the global connected components of resources whose body-expanded
    // intervals overlap on any shared lane. Runtime does not lock one entire
    // global component; it uses the smaller per-lane closures below and extends
    // only through the entity's contiguous planned route.
    TArray<int32> LocalConflictClusterParents;
    LocalConflictClusterParents.SetNumUninitialized(LocalConflicts.Num());
    for (int32 ConflictIndex = 0;
         ConflictIndex < LocalConflicts.Num();
         ++ConflictIndex)
    {
        LocalConflictClusterParents[ConflictIndex] = ConflictIndex;
    }
    const auto FindLocalConflictClusterRoot = [
        &LocalConflictClusterParents](const int32 ConflictIndex)
    {
        int32 Root = ConflictIndex;
        while (LocalConflictClusterParents[Root] != Root)
        {
            Root = LocalConflictClusterParents[Root];
        }
        int32 Current = ConflictIndex;
        while (LocalConflictClusterParents[Current] != Current)
        {
            const int32 Parent = LocalConflictClusterParents[Current];
            LocalConflictClusterParents[Current] = Root;
            Current = Parent;
        }
        return Root;
    };
    const auto GetConflictLaneInterval = [](
        const FRuntimeCentralLocalConflict& Conflict,
        const int32 LaneIndex,
        float& OutBeginDistanceCm,
        float& OutEndDistanceCm)
    {
        if (LaneIndex == Conflict.FirstLaneIndex)
        {
            OutBeginDistanceCm = Conflict.FirstBeginDistanceCm;
            OutEndDistanceCm = Conflict.FirstEndDistanceCm;
            return true;
        }
        if (LaneIndex == Conflict.SecondLaneIndex)
        {
            OutBeginDistanceCm = Conflict.SecondBeginDistanceCm;
            OutEndDistanceCm = Conflict.SecondEndDistanceCm;
            return true;
        }
        return false;
    };
    for (int32 LaneIndex = 0;
         LaneIndex < LocalConflictIndicesByLane.Num();
         ++LaneIndex)
    {
        const TArray<int32>& LaneConflicts =
            LocalConflictIndicesByLane[LaneIndex];
        for (int32 FirstListIndex = 0;
             FirstListIndex < LaneConflicts.Num();
             ++FirstListIndex)
        {
            float FirstBeginDistanceCm = 0.0f;
            float FirstEndDistanceCm = 0.0f;
            GetConflictLaneInterval(
                LocalConflicts[LaneConflicts[FirstListIndex]],
                LaneIndex,
                FirstBeginDistanceCm,
                FirstEndDistanceCm);
            for (int32 SecondListIndex = FirstListIndex + 1;
                 SecondListIndex < LaneConflicts.Num();
                 ++SecondListIndex)
            {
                float SecondBeginDistanceCm = 0.0f;
                float SecondEndDistanceCm = 0.0f;
                GetConflictLaneInterval(
                    LocalConflicts[LaneConflicts[SecondListIndex]],
                    LaneIndex,
                    SecondBeginDistanceCm,
                    SecondEndDistanceCm);
                const bool bExpandedIntervalsOverlap =
                    FirstBeginDistanceCm -
                            CentralSevereOverlapDistanceCm <=
                        SecondEndDistanceCm +
                            CentralSevereOverlapDistanceCm &&
                    SecondBeginDistanceCm -
                            CentralSevereOverlapDistanceCm <=
                        FirstEndDistanceCm +
                            CentralSevereOverlapDistanceCm;
                if (!bExpandedIntervalsOverlap)
                {
                    continue;
                }
                const int32 FirstRoot = FindLocalConflictClusterRoot(
                    LaneConflicts[FirstListIndex]);
                const int32 SecondRoot = FindLocalConflictClusterRoot(
                    LaneConflicts[SecondListIndex]);
                if (FirstRoot != SecondRoot)
                {
                    LocalConflictClusterParents[
                        FMath::Max(FirstRoot, SecondRoot)] =
                            FMath::Min(FirstRoot, SecondRoot);
                }
            }
        }
    }
    TMap<int32, int32> StableClusterIndicesByRoot;
    TArray<int32> LocalConflictClusterIndices;
    TArray<TArray<int32>> LocalConflictIndicesByCluster;
    LocalConflictClusterIndices.SetNumUninitialized(LocalConflicts.Num());
    for (int32 ConflictIndex = 0;
         ConflictIndex < LocalConflicts.Num();
         ++ConflictIndex)
    {
        const int32 Root = FindLocalConflictClusterRoot(ConflictIndex);
        int32* StableClusterIndex =
            StableClusterIndicesByRoot.Find(Root);
        if (!StableClusterIndex)
        {
            const int32 NewClusterIndex =
                StableClusterIndicesByRoot.Num();
            StableClusterIndicesByRoot.Add(Root, NewClusterIndex);
            LocalConflictIndicesByCluster.AddDefaulted();
            StableClusterIndex =
                StableClusterIndicesByRoot.Find(Root);
        }
        LocalConflictClusterIndices[ConflictIndex] =
            *StableClusterIndex;
        LocalConflictIndicesByCluster[*StableClusterIndex].Add(
            ConflictIndex);
    }
    const int32 LocalConflictClusterCount =
        LocalConflictIndicesByCluster.Num();
    TArray<TMap<int32, TArray<int32>>>
        LocalConflictClosuresByLane;
    LocalConflictClosuresByLane.SetNum(
        LocalConflictIndicesByLane.Num());
    for (int32 LaneIndex = 0;
         LaneIndex < LocalConflictIndicesByLane.Num();
         ++LaneIndex)
    {
        for (const int32 SeedConflictIndex :
             LocalConflictIndicesByLane[LaneIndex])
        {
            float ClosureBeginDistanceCm = 0.0f;
            float ClosureEndDistanceCm = 0.0f;
            GetConflictLaneInterval(
                LocalConflicts[SeedConflictIndex],
                LaneIndex,
                ClosureBeginDistanceCm,
                ClosureEndDistanceCm);
            ClosureBeginDistanceCm = FMath::Max(
                0.0f,
                ClosureBeginDistanceCm -
                    CentralSevereOverlapDistanceCm);
            ClosureEndDistanceCm +=
                CentralSevereOverlapDistanceCm;
            TArray<int32> ClosureConflictIndices;
            ClosureConflictIndices.Add(SeedConflictIndex);
            bool bAddedConflict = true;
            while (bAddedConflict)
            {
                bAddedConflict = false;
                for (const int32 ConflictIndex :
                     LocalConflictIndicesByLane[LaneIndex])
                {
                    if (ClosureConflictIndices.Contains(ConflictIndex))
                    {
                        continue;
                    }
                    float BeginDistanceCm = 0.0f;
                    float EndDistanceCm = 0.0f;
                    GetConflictLaneInterval(
                        LocalConflicts[ConflictIndex],
                        LaneIndex,
                        BeginDistanceCm,
                        EndDistanceCm);
                    BeginDistanceCm = FMath::Max(
                        0.0f,
                        BeginDistanceCm -
                            CentralSevereOverlapDistanceCm);
                    EndDistanceCm +=
                        CentralSevereOverlapDistanceCm;
                    if (BeginDistanceCm > ClosureEndDistanceCm ||
                        EndDistanceCm < ClosureBeginDistanceCm)
                    {
                        continue;
                    }
                    ClosureConflictIndices.Add(ConflictIndex);
                    ClosureBeginDistanceCm = FMath::Min(
                        ClosureBeginDistanceCm,
                        BeginDistanceCm);
                    ClosureEndDistanceCm = FMath::Max(
                        ClosureEndDistanceCm,
                        EndDistanceCm);
                    bAddedConflict = true;
                }
            }
            ClosureConflictIndices.Sort();
            LocalConflictClosuresByLane[LaneIndex].Add(
                SeedConflictIndex,
                MoveTemp(ClosureConflictIndices));
        }
    }
    CentralStrictLocalConflictPairCount = StrictLocalConflictPairCount;
    CentralUncoveredLocalConflictPairCount = StrictUncoveredPairCount;
    if (CentralUncoveredLocalConflictPairCount != 0)
    {
        return RejectCache(FString::Printf(
            TEXT("local_conflict_coverage_incomplete strict=%d uncovered=%d"),
            CentralStrictLocalConflictPairCount,
            CentralUncoveredLocalConflictPairCount));
    }

    // Count connected resources for audit only. Runtime admission uses every
    // direct edge below; it never treats the transitive component as proof that
    // two non-adjacent lane geometries conflict.
    int32 OpposingConflictPairCount = 0;
    int32 OpposingAffectedLaneCount = 0;
    for (const TArray<int32>& Conflicts : OpposingPhysicalConflicts)
    {
        OpposingConflictPairCount += Conflicts.Num();
        OpposingAffectedLaneCount += Conflicts.IsEmpty() ? 0 : 1;
    }
    OpposingConflictPairCount /= 2;

    TBitArray<> bVisitedOpposingLane(false, CertifiedLanes.Num());
    int32 OpposingCorridorCount = 0;
    for (int32 RootLaneIndex = 0;
         RootLaneIndex < CertifiedLanes.Num();
         ++RootLaneIndex)
    {
        if (OpposingPhysicalConflicts[RootLaneIndex].IsEmpty() ||
            bVisitedOpposingLane[RootLaneIndex])
        {
            continue;
        }
        TArray<int32> Queue;
        Queue.Add(RootLaneIndex);
        bVisitedOpposingLane[RootLaneIndex] = true;
        for (int32 QueueIndex = 0;
             QueueIndex < Queue.Num();
             ++QueueIndex)
        {
            const int32 LaneIndex = Queue[QueueIndex];
            for (const int32 NeighborLaneIndex :
                 OpposingPhysicalConflicts[LaneIndex])
            {
                if (!bVisitedOpposingLane[NeighborLaneIndex])
                {
                    bVisitedOpposingLane[NeighborLaneIndex] = true;
                    Queue.Add(NeighborLaneIndex);
                }
            }
        }
        ++OpposingCorridorCount;
    }

    RuntimeCentralLaneLengthsCm.Reset(CertifiedLanes.Num());
    RuntimeCentralLaneIds.Reset(CertifiedLanes.Num());
    RuntimeCentralLaneCellIds.Reset(CertifiedLanes.Num());
    RuntimeCentralLaneComponentIds.Reset(CertifiedLanes.Num());
    RuntimeCentralLaneFromNodeIds.Reset(CertifiedLanes.Num());
    RuntimeCentralLaneToNodeIds.Reset(CertifiedLanes.Num());
    RuntimeCentralPhysicalTrackIndices.Reset(CertifiedLanes.Num());
    RuntimeCentralSameDirectionPhysicalLaneIndices =
        MoveTemp(SameDirectionPhysicalConflicts);
    RuntimeCentralOpposingPhysicalLaneIndices =
        MoveTemp(OpposingPhysicalConflicts);
    RuntimeCentralLocalConflicts = MoveTemp(LocalConflicts);
    RuntimeCentralLocalConflictClusterIndices =
        MoveTemp(LocalConflictClusterIndices);
    RuntimeCentralLocalConflictIndicesByCluster =
        MoveTemp(LocalConflictIndicesByCluster);
    RuntimeCentralLocalConflictClusterCount =
        LocalConflictClusterCount;
    RuntimeCentralLocalConflictIndicesByLane =
        MoveTemp(LocalConflictIndicesByLane);
    RuntimeCentralLocalConflictClosuresByLane =
        MoveTemp(LocalConflictClosuresByLane);
    RuntimeCentralCollisionConflictLaneIndices =
        MoveTemp(CollisionConflictLaneIndices);
    RuntimeCentralReverseLaneIndices.Reset(CertifiedLanes.Num());
    RuntimeCentralCrossingFlags.Reset(CertifiedLanes.Num());
    RuntimeCentralLaneIndicesById.Reset();
    RuntimeKnownCentralCellIds.Reset();
    RuntimeUnavailableCentralCellIds.Reset();
    RuntimeCentralCellRecoveryGuardCounts.Reset();
    RuntimeCentralCellFailedGuardEntities.Reset();
    CentralAvailabilityRevision = 0;
    CentralWaitingRouteRetryCursor = 0;
    for (const TPair<FName, const FOpenMassCrowdCentralCell*>& CellPair : CellsById)
    {
        RuntimeKnownCentralCellIds.Add(CellPair.Key);
    }
    for (int32 LaneIndex = 0; LaneIndex < CertifiedLanes.Num(); ++LaneIndex)
    {
        const FOpenMassCrowdCentralDirectedLane& Lane = *CertifiedLanes[LaneIndex];
        RuntimeCentralLaneLengthsCm.Add(static_cast<float>(Lane.LengthCm));
        RuntimeCentralLaneIds.Add(Lane.LaneId);
        RuntimeCentralLaneCellIds.Add(Lane.CellId);
        RuntimeCentralLaneComponentIds.Add(Lane.ComponentId);
        RuntimeCentralLaneFromNodeIds.Add(Lane.FromNodeId);
        RuntimeCentralLaneToNodeIds.Add(Lane.ToNodeId);
        RuntimeCentralPhysicalTrackIndices.Add(
            FindPhysicalTrackRoot(LaneIndex));
        RuntimeCentralReverseLaneIndices.Add(
            LaneIndicesById.FindChecked(Lane.ReverseLaneId));
        RuntimeCentralCrossingFlags.Add(
            Lane.PedestrianClass == EOpenMassCrowdCentralPedestrianClass::Crossing ? 1 : 0);
        RuntimeCentralLaneIndicesById.Add(Lane.LaneId, LaneIndex);
    }
    TSet<int32> PhysicalTrackRoots;
    for (const int32 PhysicalTrackIndex :
         RuntimeCentralPhysicalTrackIndices)
    {
        PhysicalTrackRoots.Add(PhysicalTrackIndex);
    }
    UE_LOG(
        LogTemp,
        Warning,
        TEXT("OPEN_MASS_CROWD_CENTRAL_PHYSICAL_TRACKS lanes=%d groups=%d aliased_lanes=%d tolerance_cm=%.1f"),
        RuntimeCentralPhysicalTrackIndices.Num(),
        PhysicalTrackRoots.Num(),
        RuntimeCentralPhysicalTrackIndices.Num() - PhysicalTrackRoots.Num(),
        CentralMinimumCenterClearanceCm);
    UE_LOG(
        LogTemp,
        Warning,
        TEXT("OPEN_MASS_CROWD_CENTRAL_OPPOSING_CORRIDORS conflict_pairs=%d affected_lanes=%d corridors=%d spawn_tolerance_cm=%.1f declared_reverse_total=%d declared_reverse_included_geometry=%d declared_reverse_excluded_clear_geometry=%d declared_reverse_included_max_separation_cm=%.3f declared_reverse_excluded_min_separation_cm=%.3f direct_adjacency=true spawn_conflict_free=true occupancy_capacity=true runtime_whole_track_lock=false runtime_hard_threshold_cm=%.1f runtime_local_intervals=%d"),
        OpposingConflictPairCount,
        OpposingAffectedLaneCount,
        OpposingCorridorCount,
        CentralOpposingCorridorToleranceCm,
        DeclaredReversePairCount,
        DeclaredReverseIncludedPairCount,
        DeclaredReverseExcludedGeometryPairCount,
        DeclaredReverseIncludedMaximumSeparationCm,
        DeclaredReverseExcludedGeometryPairCount > 0
            ? DeclaredReverseExcludedMinimumSeparationCm
            : -1.0,
        CentralSevereOverlapDistanceCm,
        RuntimeOnlyOpposingIntervalCount);
    UE_LOG(
        LogTemp,
        Warning,
        TEXT("OPEN_MASS_CROWD_CENTRAL_LOCAL_CONFLICT_AUDIT strict_threshold_cm=%.2f guard_threshold_cm=%.3f maximum_sample_segment_cm=%.3f detected_strict_local_pairs=%d guarded_local_pairs=%d whole_track_covered_strict=%d interval_covered_strict=%d uncovered_strict=%d runtime_interval_resources=%d strict_whole_opposing=%d runtime_only_opposing_pairs=%d runtime_only_opposing_intervals=%d runtime_opposing_coverage_complete=%s overlap_clusters=%d same_shared=%d same_nonshared=%d opposing_shared=%d opposing_nonshared=%d declared_reverse=%d cross_shared=%d cross_nonshared=%d spatial_hash=true direct_intervals=true same_lane_closure_claims=true route_contiguous_claims=true"),
        CentralSevereOverlapDistanceCm,
        LocalConflictDetectionRadiusCm,
        MaximumCertifiedSegmentLengthCm,
        StrictLocalConflictPairCount,
        LocalConflictBuilders.Num(),
        StrictWholeTrackCoveredPairCount,
        StrictIntervalCoveredPairCount,
        StrictUncoveredPairCount,
        RuntimeCentralLocalConflicts.Num(),
        StrictWholeOpposingPairCount,
        RuntimeOnlyOpposingCoveredPairCount,
        RuntimeOnlyOpposingIntervalCount,
        RuntimeOnlyOpposingCoveredPairCount == StrictWholeOpposingPairCount
            ? TEXT("true")
            : TEXT("false"),
        RuntimeCentralLocalConflictClusterCount,
        StrictSameSharedNodePairCount,
        StrictSameNonSharedNodePairCount,
        StrictOpposingSharedNodePairCount,
        StrictOpposingNonSharedNodePairCount,
        StrictDeclaredReversePairCount,
        StrictCrossSharedNodePairCount,
        StrictCrossNonSharedNodePairCount);
    UE_LOG(
        LogTemp,
        Warning,
        TEXT("OPEN_MASS_CROWD_CENTRAL_SPACING_READY headway_cm=%.1f collision_violation_threshold_cm=%.1f node_conflict_zone_cm=%.1f local_conflict_resources=%d pre_path_follow=true transition_reservation=true local_interval_reservation=true"),
        CentralMinimumCenterClearanceCm,
        CentralSevereOverlapDistanceCm,
        CentralSevereOverlapDistanceCm,
        RuntimeCentralLocalConflicts.Num());

    TArray<const FOpenMassCrowdCentralSpawnDistrict*> StableDistricts;
    StableDistricts.Reserve(Asset.SpawnDistricts.Num());
    for (const FOpenMassCrowdCentralSpawnDistrict& District : Asset.SpawnDistricts)
    {
        StableDistricts.Add(&District);
    }
    StableDistricts.Sort(
        [](const FOpenMassCrowdCentralSpawnDistrict& A,
           const FOpenMassCrowdCentralSpawnDistrict& B)
        {
            return A.DistrictId.LexicalLess(B.DistrictId);
        });

    RuntimeCentralDistricts.Reset(StableDistricts.Num());
    for (const FOpenMassCrowdCentralSpawnDistrict* District : StableDistricts)
    {
        FRuntimeCentralDistrict& RuntimeDistrict =
            RuntimeCentralDistricts.AddDefaulted_GetRef();
        RuntimeDistrict.DistrictId = District->DistrictId;
        RuntimeDistrict.CellIds = District->CellIds;
        RuntimeDistrict.ComponentId = District->ComponentId;
        RuntimeDistrict.TargetPopulation = District->TargetPopulation;
        // Districts are population/telemetry quotas, not collision boundaries.
        // Build each quota from the same complete certified Central ground
        // network. Restricting a quota to its declared seed cell (or one-hop
        // neighbours) made nested admission pools compete for the same scarce
        // initial-packing-safe points: the smaller r0-c1 pool necessarily
        // exhausted the only viable part of r1-c1. A shared network-wide
        // candidate pool lets the existing global clearance/opposing-lane
        // checks distribute all 100 slots physically, while the stable six-district quotas remain
        // exact. Every candidate still comes from a collision-certified sample
        // and must pass the normal live Cesium support probe. Each pedestrian's
        // A* route remains confined to its actual spawn-lane component, so this
        // does not invent connectivity between cells or components.
        TSet<FName> SpawnPoolCellIds;
        for (const FName CellId : District->CellIds)
        {
            SpawnPoolCellIds.Add(CellId);
        }
        TSet<FName> PrimaryComponentIds;
        for (int32 LaneIndex = 0;
             LaneIndex < RuntimeCentralLaneComponentIds.Num();
             ++LaneIndex)
        {
            if (SpawnPoolCellIds.Contains(RuntimeCentralLaneCellIds[LaneIndex]))
            {
                PrimaryComponentIds.Add(
                    RuntimeCentralLaneComponentIds[LaneIndex]);
            }
        }
        const bool bExpandedToNetworkPool =
            SpawnPoolCellIds.Num() < Asset.Cells.Num();
        for (const FOpenMassCrowdCentralCell& CandidateCell : Asset.Cells)
        {
            SpawnPoolCellIds.Add(CandidateCell.CellId);
        }
        RuntimeDistrict.SpawnLaneIndices.Reserve(CertifiedLanes.Num());
        for (int32 LaneIndex = 0;
             LaneIndex < RuntimeCentralLaneComponentIds.Num();
             ++LaneIndex)
        {
            if (SpawnPoolCellIds.Contains(RuntimeCentralLaneCellIds[LaneIndex]))
            {
                RuntimeDistrict.SpawnLaneIndices.Add(LaneIndex);
            }
        }
        RuntimeDistrict.SpawnLaneIndices.Sort();
        UE_LOG(
            LogTemp,
            Log,
            TEXT("OPEN_MASS_CROWD_CENTRAL_DISTRICT_LANES district=%s declared=%d lane_pool=%d primary_cells=%d admission_cells=%d primary_components=%d network_pool_expansion=%s seed_component=%s route_component=actual_spawn_lane live_support_required=true"),
            *RuntimeDistrict.DistrictId.ToString(),
            District->SpawnLaneIds.Num(),
            RuntimeDistrict.SpawnLaneIndices.Num(),
            RuntimeDistrict.CellIds.Num(),
            SpawnPoolCellIds.Num(),
            PrimaryComponentIds.Num(),
            bExpandedToNetworkPool ? TEXT("true") : TEXT("false"),
            *RuntimeDistrict.ComponentId.ToString());
    }

    RuntimeZoneGraphData = GetWorld()->SpawnActor<AZoneGraphData>(
        AZoneGraphData::StaticClass(),
        FTransform::Identity);
    if (!RuntimeZoneGraphData)
    {
        return RejectCache(TEXT("runtime_zone_graph_spawn_failed"));
    }
    ZoneGraphSubsystem->UnregisterZoneGraphData(*RuntimeZoneGraphData);

    FScopeLock StorageLock(&RuntimeZoneGraphData->GetStorageLock());
    FZoneGraphStorage& Storage = RuntimeZoneGraphData->GetStorageMutable();
    Storage.Reset();
    Storage.Lanes.Reserve(CertifiedLanes.Num());
    Storage.Zones.Reserve(CertifiedLanes.Num());

    FBox RouteBounds(ForceInit);
    for (int32 LaneIndex = 0; LaneIndex < CertifiedLanes.Num(); ++LaneIndex)
    {
        const FOpenMassCrowdCentralDirectedLane& Lane = *CertifiedLanes[LaneIndex];
        TArray<FVector> LanePoints;
        LanePoints.Reserve(Lane.GroundSamples.Num());
        for (const FOpenMassCrowdCentralGroundSample& Sample : Lane.GroundSamples)
        {
            // Each strict sample contains independently collision-certified
            // left, centre and right support points.  Route a directed lane on
            // its right-hand certified track so the reverse direction naturally
            // uses the opposite physical side of the pavement.  This preserves
            // exact Cesium support while avoiding the artificial head-on
            // stacking caused by forcing both directions onto one centreline.
            LanePoints.Add(Sample.RightTrackPosition);
        }

        const TArray<int32>* OutgoingLaneIndices =
            OutgoingLaneIndicesByNode.Find(Lane.ToNodeId);
        const TArray<int32>* IncomingLaneIndices =
            IncomingLaneIndicesByNode.Find(Lane.FromNodeId);
        static const TArray<int32> EmptyLaneIndices;
        AppendLane(
            Storage,
            LanePoints,
            Lane.WidthCm,
            LaneIndex,
            OutgoingLaneIndices ? *OutgoingLaneIndices : EmptyLaneIndices,
            IncomingLaneIndices ? *IncomingLaneIndices : EmptyLaneIndices);
        Storage.Lanes.Last().Tags = FZoneGraphTagMask(
            Lane.PedestrianClass == EOpenMassCrowdCentralPedestrianClass::Crossing
                ? 3
                : 1);

        FZoneData Zone;
        Zone.BoundaryPointsBegin = Storage.BoundaryPoints.Num();
        const float HalfLaneWidth = Lane.WidthCm * 0.5f;
        for (int32 PointIndex = 0; PointIndex < LanePoints.Num(); ++PointIndex)
        {
            const int32 PreviousPointIndex = FMath::Max(PointIndex - 1, 0);
            const int32 NextPointIndex = FMath::Min(PointIndex + 1, LanePoints.Num() - 1);
            const FVector Tangent =
                (LanePoints[NextPointIndex] - LanePoints[PreviousPointIndex]).GetSafeNormal2D();
            const FVector Right = Tangent.IsNearlyZero()
                ? FVector::RightVector
                : FVector(-Tangent.Y, Tangent.X, 0.0f);
            Storage.BoundaryPoints.Add(LanePoints[PointIndex] + Right * HalfLaneWidth);
        }
        for (int32 PointIndex = LanePoints.Num() - 1; PointIndex >= 0; --PointIndex)
        {
            const int32 PreviousPointIndex = FMath::Max(PointIndex - 1, 0);
            const int32 NextPointIndex = FMath::Min(PointIndex + 1, LanePoints.Num() - 1);
            const FVector Tangent =
                (LanePoints[NextPointIndex] - LanePoints[PreviousPointIndex]).GetSafeNormal2D();
            const FVector Right = Tangent.IsNearlyZero()
                ? FVector::RightVector
                : FVector(-Tangent.Y, Tangent.X, 0.0f);
            Storage.BoundaryPoints.Add(LanePoints[PointIndex] - Right * HalfLaneWidth);
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
        Zone.Tags = Storage.Lanes.Last().Tags;
        Storage.Zones.Add(Zone);
        RouteBounds += LaneBounds;
    }

    Storage.Bounds = RouteBounds;
    FOpenMassCrowdBalancedZoneBVTree BalancedBVTree;
    BalancedBVTree.BuildBalanced(Storage.Zones);
    Storage.ZoneBVTree = MoveTemp(BalancedBVTree);
    const int32 BVNodeCount = Storage.ZoneBVTree.GetNumNodes();
    StorageLock.Unlock();

    ZoneGraphSubsystem->RegisterZoneGraphData(*RuntimeZoneGraphData);
    const FZoneGraphDataHandle DataHandle = RuntimeZoneGraphData->GetStorage().DataHandle;
    if (!DataHandle.IsValid())
    {
        return RejectCache(TEXT("runtime_zone_graph_registration_failed"));
    }

    RuntimeLaneHandles.Reset(Storage.Lanes.Num());
    for (int32 LaneIndex = 0; LaneIndex < Storage.Lanes.Num(); ++LaneIndex)
    {
        RuntimeLaneHandles.Emplace(LaneIndex, DataHandle);
    }

    FZoneGraphLaneLocation ValidationStart;
    FZoneGraphLaneLocation ValidationEnd;
    float ValidationStartLength = 0.0f;
    float ValidationEndLength = 0.0f;
    const FOpenMassCrowdCentralComponent& ValidationComponent =
        Asset.Components[0];
    const int32* ValidationStartIndex =
        LaneIndicesById.Find(ValidationComponent.DirectedLaneIds[0]);
    const int32* ValidationEndIndex =
        LaneIndicesById.Find(ValidationComponent.DirectedLaneIds.Last());
    if (!ValidationStartIndex || !ValidationEndIndex)
    {
        return RejectCache(TEXT("runtime_component_lane_query_failed"));
    }
    const FZoneGraphLaneHandle ValidationStartHandle =
        RuntimeLaneHandles[*ValidationStartIndex];
    const FZoneGraphLaneHandle ValidationEndHandle =
        RuntimeLaneHandles[*ValidationEndIndex];
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
        return RejectCache(TEXT("runtime_lane_query_failed"));
    }

    const FZoneGraphStorage& RegisteredStorage = RuntimeZoneGraphData->GetStorage();
    FZoneGraphAStarWrapper ValidationGraph(RegisteredStorage);
    FZoneGraphAStar ValidationPathfinder(ValidationGraph);
    const FZoneGraphAStarNode ValidationStartNode(
        ValidationStart.LaneHandle.Index,
        ValidationStart.Position);
    const FZoneGraphAStarNode ValidationEndNode(
        ValidationEnd.LaneHandle.Index,
        ValidationEnd.Position);
    const FZoneGraphPathFilter ValidationFilter(
        RegisteredStorage,
        ValidationStart,
        ValidationEnd);
    TArray<FZoneGraphAStarWrapper::FNodeRef> ValidationPath;
    const EGraphAStarResult ValidationResult = ValidationPathfinder.FindPath(
        ValidationStartNode,
        ValidationEndNode,
        ValidationFilter,
        ValidationPath);
    if (ValidationResult != EGraphAStarResult::SearchSuccess || ValidationPath.Num() < 2)
    {
        return RejectCache(FString::Printf(
            TEXT("runtime_astar_failed result=%d path_lanes=%d"),
            static_cast<int32>(ValidationResult),
            ValidationPath.Num()));
    }

    RuntimeNetworkNodeCount = NodesById.Num();
    UE_LOG(
        LogTemp,
        Warning,
        TEXT("OPEN_MASS_CROWD_CENTRAL_ASTAR_READY network=%s cells=%d components=%d nodes=%d lanes=%d zones=%d bv_nodes=%d validation_path_lanes=%d samples=%d whole_area_recertification=0"),
        *Asset.NetworkId.ToString(),
        Asset.Cells.Num(),
        Asset.Components.Num(),
        RuntimeNetworkNodeCount,
        RuntimeLaneHandles.Num(),
        RegisteredStorage.Zones.Num(),
        BVNodeCount,
        ValidationPath.Num(),
        TotalGroundSampleCount);
    return true;
}

void AOpenMassCrowdSpawner::SpawnMassPopulation()
{
    if (!GetWorld() || !SpawnedEntities.IsEmpty())
    {
        return;
    }

    if (NetworkMode == EOpenMassCrowdNetworkMode::CentralCertifiedCache)
    {
        // Central consumes only the pre-certified cache. Do not fall back to
        // the local 3x3 builder or perform any whole-area Cesium admission
        // work when a cache is missing, stale, or malformed.
        if (!BuildRuntimeZoneGraphFromCentralCache())
        {
            DestroyRuntimePopulation();
            UE_LOG(
                LogTemp,
                Error,
                TEXT("OPEN_MASS_CROWD_ABORT Central certified cache unavailable"));
            return;
        }
    }
    else
    {
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
    }

    if (!SpawnEntitiesOnLanes())
    {
        UE_LOG(LogTemp, Error, TEXT("OPEN_MASS_CROWD_ABORT runtime Mass/ZoneGraph setup failed"));
        DestroyRuntimePopulation();
        if (NetworkMode == EOpenMassCrowdNetworkMode::CentralCertifiedCache)
        {
            // The cache and VAT descriptors are deterministic, but the first
            // exact-XY admission probes depend on asynchronously streamed
            // Cesium collision. Retry the complete fail-closed admission; do
            // not retain a partial batch or fall back to the local network.
            ScheduleCentralSpawnRetry(TEXT("initial_admission_failed"));
        }
        return;
    }

    GroundRetryCount = 0;
    if (NetworkMode == EOpenMassCrowdNetworkMode::CentralCertifiedCache)
    {
        UE_LOG(
            LogTemp,
            Warning,
            TEXT("OPEN_MASS_CROWD_CENTRAL_ADMISSION_STARTED gate=%d admitted=%d batch_size=%d interval=%.3f districts=%d"),
            CentralAdmissionTargetCount,
            CentralAdmittedEntityCount,
            FMath::Clamp(CentralAdmissionBatchSize, 1, 50),
            FMath::Max(CentralAdmissionBatchInterval, 0.01f),
            RuntimeCentralDistricts.Num());
        return;
    }
    UE_LOG(
        LogTemp,
        Warning,
        TEXT("OPEN_MASS_CROWD_READY requested=%d spawned=%d lanes=%d cesium_network_nodes=%d center=(%.2f,%.2f,%.2f) network_mode=%s"),
        PopulationCount,
        SpawnedEntities.Num(),
        RuntimeLaneHandles.Num(),
        RuntimeNetworkNodeCount,
        GetActorLocation().X,
        GetActorLocation().Y,
        GetActorLocation().Z,
        NetworkMode == EOpenMassCrowdNetworkMode::CentralCertifiedCache
            ? TEXT("central_cache")
            : TEXT("local_certified_patch"));
}

void AOpenMassCrowdSpawner::ScheduleCentralSpawnRetry(const TCHAR* Reason)
{
    if (NetworkMode != EOpenMassCrowdNetworkMode::CentralCertifiedCache ||
        !GetWorld())
    {
        return;
    }

    ++GroundRetryCount;
    if (GroundRetryCount > MaxGroundRetries)
    {
        UE_LOG(
            LogTemp,
            Error,
            TEXT("OPEN_MASS_CROWD_ABORT Central live admission unavailable after %d retries reason=%s"),
            MaxGroundRetries,
            Reason);
        return;
    }

    if (GroundRetryCount == 1 || GroundRetryCount % 5 == 0)
    {
        UE_LOG(
            LogTemp,
            Warning,
            TEXT("OPEN_MASS_CROWD_WAIT_CENTRAL_ADMISSION retry=%d/%d reason=%s"),
            GroundRetryCount,
            MaxGroundRetries,
            Reason);
    }
    GetWorldTimerManager().SetTimer(
        SpawnRetryTimer,
        this,
        &AOpenMassCrowdSpawner::RetrySpawn,
        1.0f,
        false);
}

void AOpenMassCrowdSpawner::RetrySpawn()
{
    if (!SpawnedEntities.IsEmpty())
    {
        return;
    }

    if (NetworkMode == EOpenMassCrowdNetworkMode::CentralCertifiedCache)
    {
        // A Central retry rebuilds only from the immutable certified cache and
        // repeats bounded exact-XY admission point probes. It never performs a
        // whole-area recertification or silently switches to the local route.
        SpawnMassPopulation();
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

    struct FResolvedVATPart
    {
        FName Name;
        TObjectPtr<UStaticMesh> Mesh;
        TArray<TObjectPtr<UMaterialInterface>> MaterialOverrides;
        FTransform LocalTransform = FTransform::Identity;
        bool bCastShadows = true;
    };

    struct FResolvedVisualVariant
    {
        FName Name;
        TArray<FResolvedVATPart> VATParts;
        FAnimToTextureAutoPlayData AutoPlayData;
        TSubclassOf<AActor> HighResTemplateActor;
        TSubclassOf<AActor> LowResTemplateActor;
        bool bUseActorRepresentation = false;
        bool bHasVAT = false;
    };

    TArray<FResolvedVisualVariant> ResolvedVariants;
    ResolvedVariants.Reserve(VisualVariants.Num());
    for (const FOpenMassCrowdVisualConfig& VisualConfig : VisualVariants)
    {
        TArray<FResolvedVATPart> ResolvedVATParts;
        FAnimToTextureAutoPlayData AutoPlayData;
        bool bAutoPlayInitialized = false;
        bool bHasVAT = false;
        const bool bHasAnyVATReference =
            !VisualConfig.VATParts.IsEmpty() ||
            !VisualConfig.StaticMesh.IsNull() ||
            !VisualConfig.AnimationData.IsNull() ||
            !VisualConfig.AnimationSequence.IsNull();

        const auto ResolveVATPart = [
            &VisualConfig,
            &ResolvedVATParts,
            &AutoPlayData,
            &bAutoPlayInitialized](
                const FName PartName,
                const TSoftObjectPtr<UStaticMesh>& MeshReference,
                const TArray<TSoftObjectPtr<UMaterialInterface>>& MaterialReferences,
                const TSoftObjectPtr<UAnimToTextureDataAsset>& AnimationDataReference,
                const TSoftObjectPtr<UAnimSequence>& AnimationSequenceReference,
                const FTransform& LocalTransform,
                const bool bCastShadows) -> bool
        {
            UStaticMesh* StaticMesh = MeshReference.LoadSynchronous();
            UAnimToTextureDataAsset* AnimationData =
                AnimationDataReference.LoadSynchronous();
            UAnimSequence* AnimationSequence =
                AnimationSequenceReference.LoadSynchronous();
            if (!StaticMesh || !AnimationData || !AnimationSequence)
            {
                UE_LOG(
                    LogTemp,
                    Error,
                    TEXT("OPEN_MASS_CROWD_VISUAL_ASSET_INVALID variant=%s part=%s mesh=%s data=%s sequence=%s"),
                    *VisualConfig.VariantName.ToString(),
                    *PartName.ToString(),
                    *GetNameSafe(StaticMesh),
                    *GetNameSafe(AnimationData),
                    *GetNameSafe(AnimationSequence));
                return false;
            }

            FAnimToTextureAutoPlayData PartAutoPlayData;
            const int32 AnimationIndex =
                AnimationData->GetIndexFromAnimSequence(AnimationSequence);
            if (AnimationIndex == INDEX_NONE ||
                !UAnimToTextureInstancePlaybackLibrary::GetAutoPlayDataFromDataAsset(
                    AnimationData,
                    AnimationIndex,
                    PartAutoPlayData) ||
                PartAutoPlayData.EndFrame <= PartAutoPlayData.StartFrame)
            {
                UE_LOG(
                    LogTemp,
                    Error,
                    TEXT("OPEN_MASS_CROWD_VAT_ANIMATION_INVALID variant=%s part=%s sequence=%s index=%d"),
                    *VisualConfig.VariantName.ToString(),
                    *PartName.ToString(),
                    *GetNameSafe(AnimationSequence),
                    AnimationIndex);
                return false;
            }

            if (!bAutoPlayInitialized)
            {
                AutoPlayData = PartAutoPlayData;
                bAutoPlayInitialized = true;
            }
            else if (!FMath::IsNearlyEqual(
                         AutoPlayData.StartFrame,
                         PartAutoPlayData.StartFrame,
                         0.01f) ||
                     !FMath::IsNearlyEqual(
                         AutoPlayData.EndFrame,
                         PartAutoPlayData.EndFrame,
                         0.01f))
            {
                UE_LOG(
                    LogTemp,
                    Error,
                    TEXT("OPEN_MASS_CROWD_VAT_FRAME_RANGE_MISMATCH variant=%s part=%s expected=%.3f..%.3f actual=%.3f..%.3f"),
                    *VisualConfig.VariantName.ToString(),
                    *PartName.ToString(),
                    AutoPlayData.StartFrame,
                    AutoPlayData.EndFrame,
                    PartAutoPlayData.StartFrame,
                    PartAutoPlayData.EndFrame);
                return false;
            }

            FResolvedVATPart& ResolvedPart =
                ResolvedVATParts.AddDefaulted_GetRef();
            ResolvedPart.Name = PartName;
            ResolvedPart.Mesh = StaticMesh;
            ResolvedPart.LocalTransform = LocalTransform;
            ResolvedPart.bCastShadows = bCastShadows;
            ResolvedPart.MaterialOverrides.Reserve(MaterialReferences.Num());
            for (const TSoftObjectPtr<UMaterialInterface>& MaterialReference :
                 MaterialReferences)
            {
                ResolvedPart.MaterialOverrides.Add(
                    MaterialReference.LoadSynchronous());
            }
            return true;
        };

        bool bVATAssemblyValid = true;
        if (!VisualConfig.VATParts.IsEmpty())
        {
            ResolvedVATParts.Reserve(VisualConfig.VATParts.Num());
            for (const FOpenMassCrowdVATPartConfig& Part : VisualConfig.VATParts)
            {
                if (!ResolveVATPart(
                        Part.PartName,
                        Part.StaticMesh,
                        Part.MaterialOverrides,
                        Part.AnimationData,
                        Part.AnimationSequence,
                        Part.LocalTransform,
                        Part.bCastShadows))
                {
                    bVATAssemblyValid = false;
                    break;
                }
            }
        }
        // A VAT-only variant retains the original strict single-mesh path so
        // the proven local 30-person fallback can load old serialized levels.
        else if (bHasAnyVATReference || !VisualConfig.bUseActorRepresentation)
        {
            bVATAssemblyValid = ResolveVATPart(
                TEXT("LegacySingleMesh"),
                VisualConfig.StaticMesh,
                VisualConfig.MaterialOverrides,
                VisualConfig.AnimationData,
                VisualConfig.AnimationSequence,
                VisualConfig.LocalTransform,
                VisualConfig.bCastShadows);
        }

        if (!bVATAssemblyValid)
        {
            UE_LOG(
                LogTemp,
                Error,
                TEXT("OPEN_MASS_CROWD_VAT_ASSEMBLY_INVALID variant=%s configured_parts=%d"),
                *VisualConfig.VariantName.ToString(),
                VisualConfig.VATParts.Num());
            if (NetworkMode == EOpenMassCrowdNetworkMode::CentralCertifiedCache &&
                !VisualConfig.VATParts.IsEmpty())
            {
                // Central's bounded actor budgets rely on every explicitly
                // configured modular appearance providing a valid VAT Low LOD.
                // Falling back to LowResSpawnedActor here could create hundreds
                // of actors while still looking like a successful admission.
                UE_LOG(
                    LogTemp,
                    Error,
                    TEXT("OPEN_MASS_CROWD_CENTRAL_VAT_REQUIRED variant=%s"),
                    *VisualConfig.VariantName.ToString());
                return false;
            }
            ResolvedVATParts.Reset();
            bAutoPlayInitialized = false;
        }
        bHasVAT = bVATAssemblyValid && !ResolvedVATParts.IsEmpty();

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
        Resolved.VATParts = MoveTemp(ResolvedVATParts);
        Resolved.AutoPlayData = AutoPlayData;
        Resolved.HighResTemplateActor = HighResTemplateActor;
        Resolved.LowResTemplateActor = LowResTemplateActor;
        Resolved.bUseActorRepresentation = VisualConfig.bUseActorRepresentation;
        Resolved.bHasVAT = bHasVAT;
    }

    if (ResolvedVariants.IsEmpty())
    {
        UE_LOG(LogTemp, Error, TEXT("OPEN_MASS_CROWD_NO_VALID_VISUAL_VARIANTS"));
        return false;
    }

    const bool bCentralAdmission =
        NetworkMode == EOpenMassCrowdNetworkMode::CentralCertifiedCache;
    const int32 RequestedPopulation = bCentralAdmission
        ? GetRequestedCentralPopulation()
        : PopulationCount;
    if (RequestedPopulation <= 0)
    {
        return false;
    }

    RuntimeTraits.Reset();
    SpawnedEntities.Reset();
    SpawnedEntities.Reserve(RequestedPopulation);
    CentralRuntimeTemplateIds.Reset();
    CentralRuntimeVariantNames.Reset();
    CentralRuntimeVariantRemainingCounts.Reset();
    CentralEntityVisualVariantIndices.Reset();
    CentralEntityVisualVariantIndices.Reserve(RequestedPopulation);

    for (int32 VariantIndex = 0; VariantIndex < ResolvedVariants.Num(); ++VariantIndex)
    {
        const int32 VariantPopulation =
            RequestedPopulation / ResolvedVariants.Num() +
            (VariantIndex < RequestedPopulation % ResolvedVariants.Num() ? 1 : 0);
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
        // UE 5.7 shares one visualization LOD calculator between templates
        // with equal parameters.  These are therefore global Central limits,
        // not per-appearance quotas; dividing by six would cap the entire
        // crowd at 4/12/50 after shared-fragment deduplication.
        const bool bInvestorPresentation =
            bCentralAdmission && bInvestorDeliveryDemoEnabled;
        const int32 HighBudget = bInvestorPresentation
            ? InvestorHighActorBudget
            : (bCentralAdmission ? 24 : 500);
        const int32 MediumBudget = bInvestorPresentation
            ? InvestorLowActorBudget
            : (bCentralAdmission ? 72 : 500);
        const int32 LowBudget = bCentralAdmission ? RequestedPopulation : 500;
        const float OffDistance = bInvestorPresentation
            ? InvestorVATVisibleDistanceCm
            : 100000.0f;
        VisualizationTrait->LODParams.LODMaxCount[EMassLOD::High] =
            HighBudget;
        VisualizationTrait->LODParams.LODMaxCount[EMassLOD::Medium] =
            MediumBudget;
        VisualizationTrait->LODParams.LODMaxCount[EMassLOD::Low] =
            LowBudget;
        VisualizationTrait->LODParams.LODMaxCount[EMassLOD::Off] =
            TNumericLimits<int32>::Max();
        // Bounded demo ranges make the transition observable while keeping all
        // 30 pedestrians represented across the normal Hong Kong editor views.
        VisualizationTrait->LODParams.BaseLODDistance[EMassLOD::High] = 0.0f;
        VisualizationTrait->LODParams.BaseLODDistance[EMassLOD::Medium] = 1200.0f;
        VisualizationTrait->LODParams.BaseLODDistance[EMassLOD::Low] =
            bInvestorPresentation ? InvestorSkeletalWalkDistanceCm : 3500.0f;
        VisualizationTrait->LODParams.BaseLODDistance[EMassLOD::Off] =
            OffDistance;
        VisualizationTrait->LODParams.VisibleLODDistance[EMassLOD::High] = 0.0f;
        VisualizationTrait->LODParams.VisibleLODDistance[EMassLOD::Medium] = 1200.0f;
        VisualizationTrait->LODParams.VisibleLODDistance[EMassLOD::Low] =
            bInvestorPresentation ? InvestorSkeletalWalkDistanceCm : 3500.0f;
        VisualizationTrait->LODParams.VisibleLODDistance[EMassLOD::Off] =
            OffDistance;

        if (Resolved.bHasVAT)
        {
            for (const FResolvedVATPart& Part : Resolved.VATParts)
            {
                FMassStaticMeshInstanceVisualizationMeshDesc MeshDesc;
                MeshDesc.Mesh = Part.Mesh;
                MeshDesc.MaterialOverrides = Part.MaterialOverrides;
                MeshDesc.LocalTransform = Part.LocalTransform;
                MeshDesc.bCastShadows = Part.bCastShadows;
                MeshDesc.Mobility = EComponentMobility::Movable;
                MeshDesc.SetSignificanceRange(EMassLOD::High, EMassLOD::Max);
                VisualizationTrait->StaticMeshInstanceDesc.Meshes.Add(
                    MoveTemp(MeshDesc));
            }

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
        if (bCentralAdmission)
        {
            CentralRuntimeTemplateIds.Add(EntityTemplate.GetTemplateID());
            CentralRuntimeVariantNames.Add(Resolved.Name);
            CentralRuntimeVariantRemainingCounts.Add(VariantPopulation);
        }
        else
        {
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
        }

        if (Resolved.bHasVAT)
        {
            UE_LOG(
                LogTemp,
                Log,
                TEXT("OPEN_MASS_CROWD_VAT_VARIANT name=%s count=%d parts=%d frames=%.0f..%.0f"),
                *Resolved.Name.ToString(),
                VariantPopulation,
                Resolved.VATParts.Num(),
                Resolved.AutoPlayData.StartFrame,
                Resolved.AutoPlayData.EndFrame);
        }
        if (bCentralAdmission)
        {
            UE_LOG(
                LogTemp,
                Log,
                TEXT("OPEN_MASS_CROWD_CENTRAL_LOD_BUDGET variant=%s population=%d high=%d medium=%d low=%d vat_parts=%d"),
                *Resolved.Name.ToString(),
                VariantPopulation,
                HighBudget,
                MediumBudget,
                LowBudget,
                Resolved.VATParts.Num());
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

    if (bCentralAdmission)
    {
        return BeginCentralBatchedAdmission();
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
    CentralShortPathChunkCount = 0;
    RouteReplanCount = 0;
    GroundProjectionFailureCount = 0;
    GroundRollbackCount = 0;
    GroundCenterRecoveryCount = 0;
    GroundUnrecoverableCount = 0;
    CurrentUnsupportedVisualCount = 0;
    MaxConsecutiveGroundMisses = 0;
    CentralGroundGuardQueryCount = 0;
    CentralGroundCandidateComponentTestCount = 0;
    CentralGroundComponentCacheRefreshCount = 0;
    CentralGroundGuardBucketCursor = 0;
    CesiumGroundComponentGrid.Reset();
    CesiumGroundLargeComponents.Reset();
    CesiumGroundComponentCacheRefreshWorldTime = -1.0;
    bCesiumGroundComponentCacheInitialized = false;
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

bool AOpenMassCrowdSpawner::BeginCentralBatchedAdmission()
{
    CentralAdmissionTargetCount = GetRequestedCentralPopulation();
    if (CentralAdmissionTargetCount <= 0 ||
        RuntimeCentralDistricts.Num() != RequiredCentralSpawnDistrictCount ||
        CentralRuntimeTemplateIds.IsEmpty() ||
        CentralRuntimeTemplateIds.Num() != CentralRuntimeVariantRemainingCounts.Num())
    {
        return false;
    }

    CentralSpawnLanePlan.Reset(CentralAdmissionTargetCount);
    CentralSpawnDistancePlan.Reset(CentralAdmissionTargetCount);
    CentralSpawnPositionPlan.Reset(CentralAdmissionTargetCount);
    CentralSpawnDistrictPlan.Reset(CentralAdmissionTargetCount);
    CentralReserveSpawnSlotsByPlanIndex.Reset(CentralAdmissionTargetCount);
    CentralReserveSpawnCursorsByPlanIndex.Reset(CentralAdmissionTargetCount);
    CentralSpawnLivePositionPlan.Reset(CentralAdmissionTargetCount);
    CentralSpawnLivePositionValid.Reset(CentralAdmissionTargetCount);
    TArray<int32> DistrictQuotas;
    DistrictQuotas.SetNumZeroed(RuntimeCentralDistricts.Num());
    const TArray<int32> FullPlanDistrictQuotas = {17, 17, 17, 17, 16, 16};
    if (FullPlanDistrictQuotas.Num() != RuntimeCentralDistricts.Num())
    {
        return false;
    }
    for (int32 DistrictIndex = 0;
         DistrictIndex < RuntimeCentralDistricts.Num();
         ++DistrictIndex)
    {
        FRuntimeCentralDistrict& District = RuntimeCentralDistricts[DistrictIndex];
        if (District.TargetPopulation !=
                GetCentralFullDistrictPopulation(DistrictIndex) ||
            District.SpawnLaneIndices.IsEmpty())
        {
            return false;
        }
        District.AdmittedPopulation = 0;
        DistrictQuotas[DistrictIndex] =
            CentralAdmissionTargetCount / RequiredCentralSpawnDistrictCount +
            (DistrictIndex <
                CentralAdmissionTargetCount % RequiredCentralSpawnDistrictCount
                ? 1
                : 0);
    }
    if (CentralAdmissionTargetCount == FullCentralPopulation)
    {
        DistrictQuotas = FullPlanDistrictQuotas;
    }

    // Keep only the strict lane interior required by InitializeCentralEntity.
    // Resource-expanded endpoints are already excluded separately; an extra
    // ten-percent margin removed certified capacity without adding safety.
    constexpr float SpawnMarginFraction = 0.0f;
    TArray<TArray<FCentralSpawnSlot>> DistrictSlots;
    DistrictSlots.SetNum(RuntimeCentralDistricts.Num());
    TArray<TArray<FCentralSpawnSlot>> DistrictCandidateSlots;
    DistrictCandidateSlots.SetNum(RuntimeCentralDistricts.Num());
    TArray<FVector> PlannedSpawnPositions;
    PlannedSpawnPositions.Reserve(FullCentralPopulation);
    TArray<int32> PlannedSpawnLaneIndices;
    PlannedSpawnLaneIndices.Reserve(FullCentralPopulation);
    float MinimumPlannedSpawnClearanceCm = TNumericLimits<float>::Max();

    UZoneGraphSubsystem* ZoneGraphSubsystem =
        UWorld::GetSubsystem<UZoneGraphSubsystem>(GetWorld());
    if (!ZoneGraphSubsystem)
    {
        return false;
    }
    if (!IsValid(RuntimeZoneGraphData))
    {
        return false;
    }
    const FZoneGraphStorage& RuntimeStorage = RuntimeZoneGraphData->GetStorage();
    const auto GetCentralCirculationPairKey = [this](
        const int32 LaneIndex) -> int32
    {
        if (!RuntimeCentralReverseLaneIndices.IsValidIndex(LaneIndex) ||
            !RuntimeLaneHandles.IsValidIndex(LaneIndex))
        {
            return INDEX_NONE;
        }
        const int32 ReverseLaneIndex =
            RuntimeCentralReverseLaneIndices[LaneIndex];
        return RuntimeLaneHandles.IsValidIndex(ReverseLaneIndex)
            ? FMath::Min(LaneIndex, ReverseLaneIndex)
            : INDEX_NONE;
    };

    const auto IsInsideExpandedLocalConflictInterval = [this](
        const int32 LaneIndex,
        const float DistanceAlongLaneCm)
    {
        if (!RuntimeCentralLocalConflictIndicesByLane.IsValidIndex(LaneIndex))
        {
            return true;
        }
        for (const int32 ConflictIndex :
             RuntimeCentralLocalConflictIndicesByLane[LaneIndex])
        {
            if (!RuntimeCentralLocalConflicts.IsValidIndex(ConflictIndex))
            {
                return true;
            }
            const FRuntimeCentralLocalConflict& Conflict =
                RuntimeCentralLocalConflicts[ConflictIndex];
            if (Conflict.bRuntimeOnlyReservation)
            {
                // Whole-track 55 cm admission already excludes the opposing
                // spawn combination. Do not erase every sample in the smaller
                // 20 cm runtime interval as an additional capacity filter.
                continue;
            }
            float BeginDistanceCm = 0.0f;
            float EndDistanceCm = 0.0f;
            if (LaneIndex == Conflict.FirstLaneIndex)
            {
                BeginDistanceCm = Conflict.FirstBeginDistanceCm;
                EndDistanceCm = Conflict.FirstEndDistanceCm;
            }
            else if (LaneIndex == Conflict.SecondLaneIndex)
            {
                BeginDistanceCm = Conflict.SecondBeginDistanceCm;
                EndDistanceCm = Conflict.SecondEndDistanceCm;
            }
            else
            {
                return true;
            }
            if (DistanceAlongLaneCm >= FMath::Max(
                    0.0f,
                    BeginDistanceCm - CentralMinimumCenterClearanceCm) &&
                DistanceAlongLaneCm <=
                    EndDistanceCm + CentralMinimumCenterClearanceCm)
            {
                return true;
            }
        }
        return false;
    };

    // Build the complete 100-person slot sequence even at a lower gate.  Every
    // staged gate is therefore a stable prefix of the exact same deterministic
    // plan. Candidates are only the direction-specific discrete LanePoints
    // copied from collision-certified RightTrackPosition samples.  Do not use
    // interpolated positions between those samples: the cache promises exact
    // Cesium support at each stored sample XY, not at an arbitrary point on the
    // short chord between two samples. Greedy farthest-point selection prevents
    // the golden-ratio aliases that previously placed different entities only
    // millimetres apart at the capped legacy higher-gate values.
    TMap<FName, float> ComponentDirectionalLengthsCm;
    for (int32 LaneIndex = 0;
         LaneIndex < RuntimeLaneHandles.Num();
         ++LaneIndex)
    {
        float LaneLengthCm = 0.0f;
        if (!RuntimeCentralLaneComponentIds.IsValidIndex(LaneIndex) ||
            !ZoneGraphSubsystem->GetLaneLength(
                RuntimeLaneHandles[LaneIndex],
                LaneLengthCm) ||
            LaneLengthCm <= 0.0f)
        {
            return false;
        }
        ComponentDirectionalLengthsCm.FindOrAdd(
            RuntimeCentralLaneComponentIds[LaneIndex]) += LaneLengthCm;
    }

    // District admission pools overlap when a sparse edge cell borrows its
    // orthogonal neighbours. A raw lane-count sort is insufficient: r1-c2 is
    // numerically smaller than r1-c1, but it consumes lanes that r1-c1 shares
    // with three other districts. Allocate strict subsets first, then the
    // pools with the greatest normalized sharing pressure. This is a stable,
    // data-derived scarcity order; it does not weaken the initial whole-plan
    // clearance requirement or hard-code a district name.
    TArray<int32> SpawnPlanningDistrictIndices;
    SpawnPlanningDistrictIndices.Reserve(RuntimeCentralDistricts.Num());
    for (int32 DistrictIndex = 0;
         DistrictIndex < RuntimeCentralDistricts.Num();
         ++DistrictIndex)
    {
        SpawnPlanningDistrictIndices.Add(DistrictIndex);
    }
    TArray<TSet<int32>> PreferredSpawnLaneSets;
    PreferredSpawnLaneSets.SetNum(RuntimeCentralDistricts.Num());
    for (int32 DistrictIndex = 0;
         DistrictIndex < RuntimeCentralDistricts.Num();
         ++DistrictIndex)
    {
        for (const int32 LaneIndex :
             RuntimeCentralDistricts[DistrictIndex].SpawnLaneIndices)
        {
            if (!RuntimeCentralLaneComponentIds.IsValidIndex(LaneIndex))
            {
                continue;
            }
            const float* ComponentDirectionalLengthCm =
                ComponentDirectionalLengthsCm.Find(
                    RuntimeCentralLaneComponentIds[LaneIndex]);
            if (ComponentDirectionalLengthCm &&
                *ComponentDirectionalLengthCm >=
                    CentralPreferredSpawnComponentDirectionalMinimumCm)
            {
                PreferredSpawnLaneSets[DistrictIndex].Add(LaneIndex);
            }
        }
    }
    TArray<int32> PreferredSpawnContainmentCounts;
    TArray<int32> PreferredSpawnOverlapPressures;
    PreferredSpawnContainmentCounts.SetNumZeroed(
        RuntimeCentralDistricts.Num());
    PreferredSpawnOverlapPressures.SetNumZeroed(
        RuntimeCentralDistricts.Num());
    for (int32 DistrictIndex = 0;
         DistrictIndex < RuntimeCentralDistricts.Num();
         ++DistrictIndex)
    {
        const TSet<int32>& DistrictLaneSet =
            PreferredSpawnLaneSets[DistrictIndex];
        for (int32 OtherDistrictIndex = 0;
             OtherDistrictIndex < RuntimeCentralDistricts.Num();
             ++OtherDistrictIndex)
        {
            if (OtherDistrictIndex == DistrictIndex)
            {
                continue;
            }
            const TSet<int32>& OtherLaneSet =
                PreferredSpawnLaneSets[OtherDistrictIndex];
            bool bStrictSubset = DistrictLaneSet.Num() < OtherLaneSet.Num();
            int32 SharedLaneCount = 0;
            for (const int32 LaneIndex : DistrictLaneSet)
            {
                if (OtherLaneSet.Contains(LaneIndex))
                {
                    ++SharedLaneCount;
                }
                else
                {
                    bStrictSubset = false;
                }
            }
            PreferredSpawnOverlapPressures[DistrictIndex] +=
                SharedLaneCount;
            PreferredSpawnContainmentCounts[DistrictIndex] +=
                bStrictSubset ? 1 : 0;
        }
    }
    // The delivery cache deliberately lets sparse districts borrow the full
    // certified Central network. When every preferred pool is identical,
    // selecting each district independently is six greedy packing passes over
    // the same geometry: the early quotas can leave no packing-safe point for
    // the final quota even though a valid global 100-person packing exists.
    // Select the shared pool once, then redistribute those already-proven
    // slots back to the six stable district quotas below.
    bool bUseUnifiedSharedPoolPlanning =
        PreferredSpawnLaneSets.Num() == RequiredCentralSpawnDistrictCount &&
        !PreferredSpawnLaneSets.IsEmpty();
    if (bUseUnifiedSharedPoolPlanning)
    {
        const TSet<int32>& ReferenceLaneSet = PreferredSpawnLaneSets[0];
        for (int32 DistrictIndex = 1;
             DistrictIndex < PreferredSpawnLaneSets.Num() &&
                 bUseUnifiedSharedPoolPlanning;
             ++DistrictIndex)
        {
            const TSet<int32>& CandidateLaneSet =
                PreferredSpawnLaneSets[DistrictIndex];
            if (CandidateLaneSet.Num() != ReferenceLaneSet.Num())
            {
                bUseUnifiedSharedPoolPlanning = false;
                break;
            }
            for (const int32 LaneIndex : ReferenceLaneSet)
            {
                if (!CandidateLaneSet.Contains(LaneIndex))
                {
                    bUseUnifiedSharedPoolPlanning = false;
                    break;
                }
            }
        }
    }
    TArray<int32> SpawnPlanningQuotas = FullPlanDistrictQuotas;
    constexpr int32 UnifiedSharedPoolDistrictIndex = 0;
    if (bUseUnifiedSharedPoolPlanning)
    {
        SpawnPlanningQuotas.Init(0, RuntimeCentralDistricts.Num());
        SpawnPlanningQuotas[UnifiedSharedPoolDistrictIndex] =
            FullCentralPopulation;
        UE_LOG(
            LogTemp,
            Log,
            TEXT("OPEN_MASS_CROWD_CENTRAL_UNIFIED_SPAWN_PLAN population=%d shared_lanes=%d districts=%d clearance_cm=%.1f"),
            FullCentralPopulation,
            PreferredSpawnLaneSets[UnifiedSharedPoolDistrictIndex].Num(),
            RuntimeCentralDistricts.Num(),
            CentralInitialPackingClearanceCm);
    }
    const auto GetPreferredSpawnLaneCapacity =
        [&PreferredSpawnLaneSets](const int32 DistrictIndex)
    {
        return PreferredSpawnLaneSets[DistrictIndex].Num();
    };
    SpawnPlanningDistrictIndices.Sort(
        [this,
         &SpawnPlanningQuotas,
         &GetPreferredSpawnLaneCapacity,
         &PreferredSpawnContainmentCounts,
         &PreferredSpawnOverlapPressures](
            const int32 LeftDistrictIndex,
            const int32 RightDistrictIndex)
        {
            const int32 LeftContainmentCount =
                PreferredSpawnContainmentCounts[LeftDistrictIndex];
            const int32 RightContainmentCount =
                PreferredSpawnContainmentCounts[RightDistrictIndex];
            if (LeftContainmentCount != RightContainmentCount)
            {
                return LeftContainmentCount > RightContainmentCount;
            }
            const int64 LeftCapacity =
                GetPreferredSpawnLaneCapacity(LeftDistrictIndex);
            const int64 RightCapacity =
                GetPreferredSpawnLaneCapacity(RightDistrictIndex);
            const int64 LeftPressure =
                PreferredSpawnOverlapPressures[LeftDistrictIndex];
            const int64 RightPressure =
                PreferredSpawnOverlapPressures[RightDistrictIndex];
            const int64 LeftScaledPressure = LeftPressure * RightCapacity;
            const int64 RightScaledPressure = RightPressure * LeftCapacity;
            if (LeftScaledPressure != RightScaledPressure)
            {
                return LeftScaledPressure > RightScaledPressure;
            }
            const int64 LeftQuota = SpawnPlanningQuotas[
                LeftDistrictIndex];
            const int64 RightQuota = SpawnPlanningQuotas[
                RightDistrictIndex];
            const int64 LeftScaledCapacity = LeftCapacity * RightQuota;
            const int64 RightScaledCapacity = RightCapacity * LeftQuota;
            if (LeftScaledCapacity != RightScaledCapacity)
            {
                return LeftScaledCapacity < RightScaledCapacity;
            }
            return RuntimeCentralDistricts[LeftDistrictIndex].DistrictId
                .LexicalLess(
                    RuntimeCentralDistricts[RightDistrictIndex].DistrictId);
        });

    for (int32 PlanningOrderIndex = 0;
         PlanningOrderIndex < SpawnPlanningDistrictIndices.Num();
         ++PlanningOrderIndex)
    {
        const int32 DistrictIndex =
            SpawnPlanningDistrictIndices[PlanningOrderIndex];
        const FRuntimeCentralDistrict& District =
            RuntimeCentralDistricts[DistrictIndex];
        const int32 RequiredDistrictSlots =
            SpawnPlanningQuotas[DistrictIndex];
        TArray<FCentralSpawnSlot>& Candidates =
            DistrictCandidateSlots[DistrictIndex];
        UE_LOG(
            LogTemp,
            Log,
            TEXT("OPEN_MASS_CROWD_CENTRAL_SPAWN_PLANNING_ORDER order=%d district=%s lane_pool=%d preferred_lane_pool=%d containment=%d overlap_pressure=%d quota=%d scarcity_first=true"),
            PlanningOrderIndex,
            *District.DistrictId.ToString(),
            District.SpawnLaneIndices.Num(),
            GetPreferredSpawnLaneCapacity(DistrictIndex),
            PreferredSpawnContainmentCounts[DistrictIndex],
            PreferredSpawnOverlapPressures[DistrictIndex],
            RequiredDistrictSlots);
        for (const int32 LaneIndex : District.SpawnLaneIndices)
        {
            if (!RuntimeLaneHandles.IsValidIndex(LaneIndex) ||
                !RuntimeCentralLaneComponentIds.IsValidIndex(LaneIndex) ||
                !RuntimeCentralOpposingPhysicalLaneIndices.IsValidIndex(
                    LaneIndex))
            {
                return false;
            }
            const float* ComponentDirectionalLengthCm =
                ComponentDirectionalLengthsCm.Find(
                    RuntimeCentralLaneComponentIds[LaneIndex]);
            if (!ComponentDirectionalLengthCm ||
                *ComponentDirectionalLengthCm <
                    CentralPreferredSpawnComponentDirectionalMinimumCm)
            {
                // Prefer the eight substantial ground components (at least
                // 90 m summed directional / about 45 m physical length).
                // The six 60 m-capable components remain the route planner's
                // first choice; the two capacity components keep every
                // district able to admit its complete 100-person quota.
                continue;
            }
            const FZoneGraphLaneHandle LaneHandle = RuntimeLaneHandles[LaneIndex];
            if (!RuntimeStorage.Lanes.IsValidIndex(LaneHandle.Index))
            {
                return false;
            }
            const FZoneLaneData& StorageLane = RuntimeStorage.Lanes[LaneHandle.Index];
            if (StorageLane.PointsBegin < 0 ||
                StorageLane.PointsEnd <= StorageLane.PointsBegin ||
                !RuntimeStorage.LanePoints.IsValidIndex(StorageLane.PointsBegin) ||
                !RuntimeStorage.LanePoints.IsValidIndex(StorageLane.PointsEnd - 1) ||
                !RuntimeStorage.LanePointProgressions.IsValidIndex(StorageLane.PointsBegin) ||
                !RuntimeStorage.LanePointProgressions.IsValidIndex(StorageLane.PointsEnd - 1))
            {
                return false;
            }
            float LaneLength = 0.0f;
            if (!ZoneGraphSubsystem->GetLaneLength(
                    LaneHandle,
                    LaneLength) ||
                LaneLength <= 1.0f)
            {
                return false;
            }
            if constexpr (bUseCentralCertifiedEdgeCirculation)
            {
                if (LaneLength < CentralMinimumEdgeCirculationLaneLengthCm)
                {
                    continue;
                }
            }
            const float MinimumCandidateDistance = LaneLength * SpawnMarginFraction;
            const float MaximumCandidateDistance =
                LaneLength * (1.0f - SpawnMarginFraction);
            for (int32 PointIndex = StorageLane.PointsBegin;
                 PointIndex < StorageLane.PointsEnd;
                 ++PointIndex)
            {
                const float CandidateDistance =
                    RuntimeStorage.LanePointProgressions[PointIndex];
                if (!FMath::IsFinite(CandidateDistance) ||
                    CandidateDistance <= MinimumCandidateDistance ||
                    CandidateDistance >= MaximumCandidateDistance ||
                    IsInsideExpandedLocalConflictInterval(
                        LaneIndex,
                        CandidateDistance))
                {
                    continue;
                }
                FCentralSpawnSlot& Candidate = Candidates.AddDefaulted_GetRef();
                Candidate.LaneIndex = LaneIndex;
                Candidate.DistanceAlongLane = CandidateDistance;
                Candidate.Position = RuntimeStorage.LanePoints[PointIndex];
            }
        }
        if (Candidates.Num() < RequiredDistrictSlots)
        {
            UE_LOG(
                LogTemp,
                Error,
                TEXT("OPEN_MASS_CROWD_CENTRAL_SPAWN_SLOT_REJECT district=%s candidates=%d required=%d"),
                *District.DistrictId.ToString(),
                Candidates.Num(),
                RequiredDistrictSlots);
            return false;
        }

        // Precompute distance to slots accepted in earlier districts. Each
        // seeded attempt then maintains one nearest-selected distance per
        // candidate, reducing a retry from O(C*N^2) to O(C*N).
        TArray<float> PreviousDistrictDistanceSquared;
        PreviousDistrictDistanceSquared.Init(
            TNumericLimits<float>::Max(),
            Candidates.Num());
        for (int32 CandidateIndex = 0;
             CandidateIndex < Candidates.Num();
             ++CandidateIndex)
        {
            for (const FVector& PlannedPosition : PlannedSpawnPositions)
            {
                PreviousDistrictDistanceSquared[CandidateIndex] = FMath::Min(
                    PreviousDistrictDistanceSquared[CandidateIndex],
                    FVector::DistSquared2D(
                        Candidates[CandidateIndex].Position,
                        PlannedPosition));
            }
        }

        TArray<FCentralSpawnSlot>& SelectedSlots = DistrictSlots[DistrictIndex];
        SelectedSlots.Reserve(RequiredDistrictSlots);
        int32 AcceptedSeed = INDEX_NONE;
        float AcceptedMinimumDistanceSquared = -1.0f;
        float BestRejectedMinimumDistanceSquared = -1.0f;
        int32 BestRejectedSeed = INDEX_NONE;
        const float RequiredDistanceSquared =
            FMath::Square(
                CentralMinimumAcceptedInitialPackingClearanceCm);
        TBitArray<> bGloballyOpposingForbidden(false, Candidates.Num());
        for (int32 CandidateIndex = 0;
             CandidateIndex < Candidates.Num();
             ++CandidateIndex)
        {
            const int32 CandidatePairKey = GetCentralCirculationPairKey(
                Candidates[CandidateIndex].LaneIndex);
            if (CandidatePairKey == INDEX_NONE)
            {
                return false;
            }
            const TArray<int32>& OpposingLanes =
                RuntimeCentralOpposingPhysicalLaneIndices[
                    Candidates[CandidateIndex].LaneIndex];
            int32 ExistingPairUseCount = 0;
            for (const int32 PlannedLaneIndex : PlannedSpawnLaneIndices)
            {
                if (CandidatePairKey ==
                    GetCentralCirculationPairKey(PlannedLaneIndex))
                {
                    ++ExistingPairUseCount;
                    // A circulation pair may carry two pedestrians only when
                    // both start on the same directed track. This creates a
                    // following platoon, never an initial head-on pair.
                    if (Candidates[CandidateIndex].LaneIndex !=
                        PlannedLaneIndex)
                    {
                        bGloballyOpposingForbidden[CandidateIndex] = true;
                        break;
                    }
                }
                if (OpposingLanes.Contains(PlannedLaneIndex))
                {
                    bGloballyOpposingForbidden[CandidateIndex] = true;
                    break;
                }
            }
            if (ExistingPairUseCount >=
                CentralMaximumPedestriansPerEdgeCirculation)
            {
                bGloballyOpposingForbidden[CandidateIndex] = true;
            }
        }
        // Seed zero exactly preserves the original deterministic plan when it
        // already passes.  Otherwise scan stable candidate indices and accept
        // the first complete quota-sized farthest-point plan above the exact
        // initial-packing threshold, with
        // every whole-track opposing direction excluded from the full 100-slot
        // plan before any bounded admission begins.
        for (int32 SeedCandidateIndex = 0;
             SeedCandidateIndex < Candidates.Num();
             ++SeedCandidateIndex)
        {
            if (bGloballyOpposingForbidden[SeedCandidateIndex])
            {
                continue;
            }
            TArray<float> NearestSelectedDistanceSquared =
                PreviousDistrictDistanceSquared;
            TBitArray<> bSelected(false, Candidates.Num());
            TBitArray<> bOpposingForbidden =
                bGloballyOpposingForbidden;
            TMap<int32, int32> CirculationPairUseCounts;
            for (const int32 PlannedLaneIndex : PlannedSpawnLaneIndices)
            {
                ++CirculationPairUseCounts.FindOrAdd(
                    GetCentralCirculationPairKey(PlannedLaneIndex));
            }
            TArray<FCentralSpawnSlot> AttemptSlots;
            AttemptSlots.Reserve(RequiredDistrictSlots);
            float AttemptMinimumDistanceSquared =
                TNumericLimits<float>::Max();
            int32 CandidateToSelect = SeedCandidateIndex;

            for (int32 SlotIndex = 0;
                 SlotIndex < RequiredDistrictSlots;
                 ++SlotIndex)
            {
                if (SlotIndex > 0)
                {
                    CandidateToSelect = INDEX_NONE;
                    float FarthestDistanceSquared = -1.0f;
                    for (int32 CandidateIndex = 0;
                         CandidateIndex < Candidates.Num();
                         ++CandidateIndex)
                    {
                        if (!bSelected[CandidateIndex] &&
                            !bOpposingForbidden[CandidateIndex] &&
                            (CandidateToSelect == INDEX_NONE ||
                             NearestSelectedDistanceSquared[CandidateIndex] >
                                 FarthestDistanceSquared))
                        {
                            CandidateToSelect = CandidateIndex;
                            FarthestDistanceSquared =
                                NearestSelectedDistanceSquared[CandidateIndex];
                        }
                    }
                }
                if (CandidateToSelect == INDEX_NONE ||
                    bSelected[CandidateToSelect])
                {
                    break;
                }

                AttemptMinimumDistanceSquared = FMath::Min(
                    AttemptMinimumDistanceSquared,
                    NearestSelectedDistanceSquared[CandidateToSelect]);
                if (AttemptMinimumDistanceSquared < RequiredDistanceSquared)
                {
                    // Adding more slots can only reduce this minimum.
                    break;
                }

                bSelected[CandidateToSelect] = true;
                const FCentralSpawnSlot& Selected =
                    Candidates[CandidateToSelect];
                AttemptSlots.Add(Selected);
                const int32 SelectedPairKey =
                    GetCentralCirculationPairKey(Selected.LaneIndex);
                if (SelectedPairKey == INDEX_NONE)
                {
                    return false;
                }
                const int32 SelectedPairUseCount =
                    ++CirculationPairUseCounts.FindOrAdd(SelectedPairKey);
                const int32 SelectedReverseLaneIndex =
                    RuntimeCentralReverseLaneIndices[Selected.LaneIndex];
                const TArray<int32>& SelectedOpposingLanes =
                    RuntimeCentralOpposingPhysicalLaneIndices[
                        Selected.LaneIndex];
                for (int32 CandidateIndex = 0;
                     CandidateIndex < Candidates.Num();
                     ++CandidateIndex)
                {
                    const bool bPairAtCapacity =
                        SelectedPairUseCount >=
                            CentralMaximumPedestriansPerEdgeCirculation &&
                        SelectedPairKey == GetCentralCirculationPairKey(
                            Candidates[CandidateIndex].LaneIndex);
                    const bool bReverseDirection =
                        Candidates[CandidateIndex].LaneIndex ==
                            SelectedReverseLaneIndex;
                    if (bPairAtCapacity || bReverseDirection ||
                        SelectedOpposingLanes.Contains(
                            Candidates[CandidateIndex].LaneIndex))
                    {
                        bOpposingForbidden[CandidateIndex] = true;
                    }
                    if (!bSelected[CandidateIndex])
                    {
                        NearestSelectedDistanceSquared[CandidateIndex] =
                            FMath::Min(
                                NearestSelectedDistanceSquared[CandidateIndex],
                                FVector::DistSquared2D(
                                    Candidates[CandidateIndex].Position,
                                    Selected.Position));
                    }
                }
            }

            if (AttemptSlots.Num() == RequiredDistrictSlots)
            {
                SelectedSlots = MoveTemp(AttemptSlots);
                AcceptedSeed = SeedCandidateIndex;
                AcceptedMinimumDistanceSquared =
                    AttemptMinimumDistanceSquared;
                break;
            }
            if (AttemptMinimumDistanceSquared >
                BestRejectedMinimumDistanceSquared)
            {
                BestRejectedMinimumDistanceSquared =
                    AttemptMinimumDistanceSquared;
                BestRejectedSeed = SeedCandidateIndex;
            }
        }

        if (AcceptedSeed == INDEX_NONE)
        {
            UE_LOG(
                LogTemp,
                Error,
                TEXT("OPEN_MASS_CROWD_CENTRAL_SPAWN_SLOT_REJECT district=%s candidates=%d required_slots=%d required_clearance_cm=%.3f best_seed=%d best_clearance_cm=%.3f"),
                *District.DistrictId.ToString(),
                Candidates.Num(),
                RequiredDistrictSlots,
                CentralInitialPackingClearanceCm,
                BestRejectedSeed,
                BestRejectedMinimumDistanceSquared >= 0.0f
                    ? FMath::Sqrt(BestRejectedMinimumDistanceSquared)
                    : -1.0f);
            return false;
        }

        const float AcceptedMinimumDistanceCm =
            AcceptedMinimumDistanceSquared == TNumericLimits<float>::Max()
            ? TNumericLimits<float>::Max()
            : FMath::Sqrt(AcceptedMinimumDistanceSquared);
        MinimumPlannedSpawnClearanceCm = FMath::Min(
            MinimumPlannedSpawnClearanceCm,
            AcceptedMinimumDistanceCm);
        for (const FCentralSpawnSlot& Selected : SelectedSlots)
        {
            PlannedSpawnPositions.Add(Selected.Position);
            PlannedSpawnLaneIndices.Add(Selected.LaneIndex);
        }
        TMap<FName, int32> SelectedComponentCounts;
        for (const FCentralSpawnSlot& Selected : SelectedSlots)
        {
            if (RuntimeCentralLaneComponentIds.IsValidIndex(
                    Selected.LaneIndex))
            {
                ++SelectedComponentCounts.FindOrAdd(
                    RuntimeCentralLaneComponentIds[Selected.LaneIndex]);
            }
        }
        TArray<FName> SelectedComponentIds;
        SelectedComponentCounts.GetKeys(SelectedComponentIds);
        SelectedComponentIds.Sort(
            [](const FName A, const FName B)
            {
                return A.LexicalLess(B);
            });
        FString SelectedComponents;
        for (const FName ComponentId : SelectedComponentIds)
        {
            if (!SelectedComponents.IsEmpty())
            {
                SelectedComponents += TEXT(",");
            }
            SelectedComponents += FString::Printf(
                TEXT("%s:%d"),
                *ComponentId.ToString(),
                SelectedComponentCounts.FindChecked(ComponentId));
        }
        UE_LOG(
            LogTemp,
            Log,
            TEXT("OPEN_MASS_CROWD_CENTRAL_SPAWN_SEED district=%s cells=%d seed_component=%s actual_spawn_components=%s seed=%d candidates=%d slots=%d minimum_clearance_cm=%.3f required_cm=%.3f"),
            *District.DistrictId.ToString(),
            District.CellIds.Num(),
            *District.ComponentId.ToString(),
            *SelectedComponents,
            AcceptedSeed,
            Candidates.Num(),
            SelectedSlots.Num(),
            AcceptedMinimumDistanceCm,
            CentralInitialPackingClearanceCm);
    }

    if (bUseUnifiedSharedPoolPlanning)
    {
        TArray<FCentralSpawnSlot> UnifiedSlots = MoveTemp(
            DistrictSlots[UnifiedSharedPoolDistrictIndex]);
        if (UnifiedSlots.Num() != FullCentralPopulation)
        {
            return false;
        }
        for (TArray<FCentralSpawnSlot>& Slots : DistrictSlots)
        {
            Slots.Reset();
        }
        int32 UnifiedSlotIndex = 0;
        while (UnifiedSlotIndex < UnifiedSlots.Num())
        {
            bool bAssignedSlot = false;
            for (int32 DistrictIndex = 0;
                 DistrictIndex < DistrictSlots.Num() &&
                     UnifiedSlotIndex < UnifiedSlots.Num();
                 ++DistrictIndex)
            {
                if (DistrictSlots[DistrictIndex].Num() >=
                    FullPlanDistrictQuotas[DistrictIndex])
                {
                    continue;
                }
                DistrictSlots[DistrictIndex].Add(
                    UnifiedSlots[UnifiedSlotIndex++]);
                bAssignedSlot = true;
            }
            if (!bAssignedSlot)
            {
                return false;
            }
        }
    }

    // Farthest-point selection proves clearance for the complete 100-person
    // set, but its order can cluster the small staged gates in one connected
    // component.  That made all five Gate30 pedestrians in r1-c2 enter the
    // same ten-lane funnel even though two other certified components were
    // already present in the accepted set.  Reorder only the already-proven
    // slots, round-robin by component, so every stable prefix exercises the
    // available street components before adding a second pedestrian to one.
    // Positions, lanes, per-district totals, and complete-plan clearance stay
    // byte-for-byte unchanged as sets.
    for (int32 DistrictIndex = 0;
         DistrictIndex < DistrictSlots.Num();
         ++DistrictIndex)
    {
        TMap<FName, TArray<FCentralSpawnSlot>> SlotsByComponent;
        for (const FCentralSpawnSlot& Slot : DistrictSlots[DistrictIndex])
        {
            if (!RuntimeCentralLaneComponentIds.IsValidIndex(Slot.LaneIndex))
            {
                return false;
            }
            SlotsByComponent.FindOrAdd(
                RuntimeCentralLaneComponentIds[Slot.LaneIndex]).Add(Slot);
        }
        TArray<FName> ComponentIds;
        SlotsByComponent.GetKeys(ComponentIds);
        ComponentIds.Sort(
            [](const FName A, const FName B)
            {
                return A.LexicalLess(B);
            });

        TArray<FCentralSpawnSlot> ComponentRoundRobinSlots;
        ComponentRoundRobinSlots.Reserve(DistrictSlots[DistrictIndex].Num());
        for (int32 ComponentSlotIndex = 0;
             ComponentRoundRobinSlots.Num() <
                 DistrictSlots[DistrictIndex].Num();
             ++ComponentSlotIndex)
        {
            bool bAddedComponentSlot = false;
            for (const FName ComponentId : ComponentIds)
            {
                const TArray<FCentralSpawnSlot>& ComponentSlots =
                    SlotsByComponent.FindChecked(ComponentId);
                if (ComponentSlots.IsValidIndex(ComponentSlotIndex))
                {
                    ComponentRoundRobinSlots.Add(
                        ComponentSlots[ComponentSlotIndex]);
                    bAddedComponentSlot = true;
                }
            }
            if (!bAddedComponentSlot)
            {
                return false;
            }
        }
        DistrictSlots[DistrictIndex] = MoveTemp(ComponentRoundRobinSlots);
    }

    // Build the complete stable round-robin ordering first. Gate30, Gate100 and
    // lower gates are literal prefixes of this exact 100-person plan, including
    // asymmetric final 51,51,47,51,50,50 district quotas.
    TArray<FCentralSpawnSlot> FullOrderedSlots;
    TArray<int32> FullOrderedDistricts;
    TArray<int32> FullOrderedDistrictCounts;
    TArray<TBitArray<>> FullOrderedConsumedSlots;
    TMap<FName, int32> FullOrderedComponentCounts;
    FullOrderedSlots.Reserve(FullCentralPopulation);
    FullOrderedDistricts.Reserve(FullCentralPopulation);
    FullOrderedDistrictCounts.SetNumZeroed(RuntimeCentralDistricts.Num());
    FullOrderedConsumedSlots.SetNum(RuntimeCentralDistricts.Num());
    for (int32 DistrictIndex = 0;
         DistrictIndex < DistrictSlots.Num();
         ++DistrictIndex)
    {
        FullOrderedConsumedSlots[DistrictIndex].Init(
            false,
            DistrictSlots[DistrictIndex].Num());
    }
    while (FullOrderedSlots.Num() < FullCentralPopulation)
    {
        bool bAddedSlot = false;
        for (int32 DistrictIndex = 0;
             DistrictIndex < RuntimeCentralDistricts.Num() &&
                 FullOrderedSlots.Num() < FullCentralPopulation;
             ++DistrictIndex)
        {
            if (FullOrderedDistrictCounts[DistrictIndex] >=
                FullPlanDistrictQuotas[DistrictIndex])
            {
                continue;
            }
            // Balance connected components across the complete global prefix,
            // not only inside each district. Adjacent-cell resilience means
            // several districts may legitimately contain the same component;
            // blindly taking local slot N can then place multiple Gate30
            // pedestrians on one short route. Prefer the least-used component
            // globally, with the existing deterministic local order as the
            // tie-breaker. With the current Central cache the first thirty
            // slots can consequently use thirty different components.
            int32 LocalSpawnIndex = INDEX_NONE;
            int32 BestGlobalComponentUseCount = MAX_int32;
            for (int32 CandidateLocalIndex = 0;
                 CandidateLocalIndex < DistrictSlots[DistrictIndex].Num();
                 ++CandidateLocalIndex)
            {
                if (FullOrderedConsumedSlots[DistrictIndex][CandidateLocalIndex])
                {
                    continue;
                }
                const FCentralSpawnSlot& CandidateSlot =
                    DistrictSlots[DistrictIndex][CandidateLocalIndex];
                if (!RuntimeCentralLaneComponentIds.IsValidIndex(
                        CandidateSlot.LaneIndex))
                {
                    return false;
                }
                const int32 ComponentUseCount =
                    FullOrderedComponentCounts.FindRef(
                        RuntimeCentralLaneComponentIds[
                            CandidateSlot.LaneIndex]);
                if (LocalSpawnIndex == INDEX_NONE ||
                    ComponentUseCount < BestGlobalComponentUseCount)
                {
                    LocalSpawnIndex = CandidateLocalIndex;
                    BestGlobalComponentUseCount = ComponentUseCount;
                }
            }
            if (!DistrictSlots[DistrictIndex].IsValidIndex(LocalSpawnIndex))
            {
                return false;
            }
            FullOrderedConsumedSlots[DistrictIndex][LocalSpawnIndex] = true;
            const FCentralSpawnSlot& OrderedSlot =
                DistrictSlots[DistrictIndex][LocalSpawnIndex];
            ++FullOrderedDistrictCounts[DistrictIndex];
            ++FullOrderedComponentCounts.FindOrAdd(
                RuntimeCentralLaneComponentIds[OrderedSlot.LaneIndex]);
            FullOrderedSlots.Add(OrderedSlot);
            FullOrderedDistricts.Add(DistrictIndex);
            bAddedSlot = true;
        }
        if (!bAddedSlot)
        {
            return false;
        }
    }
    if (FullOrderedSlots.Num() != FullCentralPopulation ||
        FullOrderedDistricts.Num() != FullCentralPopulation ||
        FullOrderedDistrictCounts != FullPlanDistrictQuotas)
    {
        return false;
    }

    TSet<FName> Gate30ComponentIds;
    const int32 Gate30PrefixSlotCount = FMath::Min(30, FullOrderedSlots.Num());
    for (int32 PlanIndex = 0;
         PlanIndex < Gate30PrefixSlotCount;
         ++PlanIndex)
    {
        Gate30ComponentIds.Add(RuntimeCentralLaneComponentIds[
            FullOrderedSlots[PlanIndex].LaneIndex]);
    }
    UE_LOG(
        LogTemp,
        Warning,
        TEXT("OPEN_MASS_CROWD_CENTRAL_PREFIX_COMPONENT_AUDIT gate=30 slots=%d unique_components=%d repeated_slots=%d global_component_balancing=true"),
        Gate30PrefixSlotCount,
        Gate30ComponentIds.Num(),
        Gate30PrefixSlotCount - Gate30ComponentIds.Num());

    TArray<int32> CurrentGateDistrictCounts;
    CurrentGateDistrictCounts.SetNumZeroed(RuntimeCentralDistricts.Num());
    for (int32 PlanIndex = 0;
         PlanIndex < CentralAdmissionTargetCount;
         ++PlanIndex)
    {
        const FCentralSpawnSlot& Slot = FullOrderedSlots[PlanIndex];
        const int32 DistrictIndex = FullOrderedDistricts[PlanIndex];
        CentralSpawnLanePlan.Add(Slot.LaneIndex);
        CentralSpawnDistancePlan.Add(Slot.DistanceAlongLane);
        CentralSpawnPositionPlan.Add(Slot.Position);
        CentralSpawnDistrictPlan.Add(DistrictIndex);
        ++CurrentGateDistrictCounts[DistrictIndex];
    }
    if (CurrentGateDistrictCounts != DistrictQuotas)
    {
        return false;
    }

    // A reserve is derived only from the same in-memory certified runtime lane
    // pools used above. Determine which complete-plan slot(s), if any, block
    // each candidate. A candidate with no blocker can replace any slot in its
    // district; a candidate with exactly one blocker may replace only that
    // The offline full plan is only a deterministic starting proposal. Cesium
    // LOD/content can change between cache creation and runtime, so every exact
    // certified sample in a district remains eligible for the incremental live
    // plan. Runtime admission proves each selected candidate against the already
    // committed prefix; stale future proposals never remove real alternatives.
    TArray<TArray<FCentralSpawnSlot>> GeneralReserveSlotsByDistrict;
    GeneralReserveSlotsByDistrict.SetNum(RuntimeCentralDistricts.Num());
    TArray<int32> UniqueUsableReserveCountsByDistrict;
    UniqueUsableReserveCountsByDistrict.SetNumZeroed(
        RuntimeCentralDistricts.Num());
    for (int32 DistrictIndex = 0;
         DistrictIndex < DistrictCandidateSlots.Num();
         ++DistrictIndex)
    {
        TArray<FCentralSpawnSlot>& DistrictReserves =
            GeneralReserveSlotsByDistrict[DistrictIndex];
        DistrictReserves.Reserve(
            DistrictCandidateSlots[DistrictIndex].Num());
        for (const FCentralSpawnSlot& Candidate :
             DistrictCandidateSlots[DistrictIndex])
        {
            if (!RuntimeCentralOpposingPhysicalLaneIndices.IsValidIndex(
                    Candidate.LaneIndex) ||
                GetCentralCirculationPairKey(Candidate.LaneIndex) == INDEX_NONE)
            {
                return false;
            }
            FCentralSpawnSlot ReserveCandidate = Candidate;
            ReserveCandidate.MinimumOtherPlanClearanceSquared =
                TNumericLimits<float>::Max();
            DistrictReserves.Add(ReserveCandidate);
        }
        UniqueUsableReserveCountsByDistrict[DistrictIndex] =
            DistrictReserves.Num();
    }
    for (int32 DistrictIndex = 0;
         DistrictIndex < UniqueUsableReserveCountsByDistrict.Num();
         ++DistrictIndex)
    {
        if (UniqueUsableReserveCountsByDistrict[DistrictIndex] <= 0)
        {
            UE_LOG(
                LogTemp,
                Error,
                TEXT("OPEN_MASS_CROWD_CENTRAL_RESERVE_REJECT district=%s reason=no_collision_safe_certified_alternative"),
                *RuntimeCentralDistricts[DistrictIndex].DistrictId.ToString());
            return false;
        }
    }

    CentralReserveSpawnSlotsByPlanIndex.SetNum(
        CentralAdmissionTargetCount);
    CentralReserveSpawnCursorsByPlanIndex.Init(
        0,
        CentralAdmissionTargetCount);
    CentralSpawnLivePositionPlan.SetNumZeroed(CentralAdmissionTargetCount);
    CentralSpawnLivePositionValid.Init(0, CentralAdmissionTargetCount);
    CentralReserveSpawnSlotCount = 0;
    int32 PlanSlotsWithReserves = 0;
    TArray<int32> PlanScopedReserveCountsByDistrict;
    PlanScopedReserveCountsByDistrict.SetNumZeroed(
        RuntimeCentralDistricts.Num());
    for (int32 PlanIndex = 0;
         PlanIndex < CentralAdmissionTargetCount;
         ++PlanIndex)
    {
        const int32 DistrictIndex = FullOrderedDistricts[PlanIndex];
        const FCentralSpawnSlot& Original = FullOrderedSlots[PlanIndex];
        const TArray<FCentralSpawnSlot>& Eligible =
            GeneralReserveSlotsByDistrict[DistrictIndex];
        TArray<FCentralSpawnSlot>& Reserves =
            CentralReserveSpawnSlotsByPlanIndex[PlanIndex];
        const int32 DesiredReserveCount = FMath::Min(
            Eligible.Num(),
            CentralMaximumReserveSlotsPerPlanIndex);
        Reserves.Reserve(DesiredReserveCount);
        const int32 StableOffset = Eligible.IsEmpty()
            ? 0
            : static_cast<int32>(
                (static_cast<int64>(PlanIndex) * 7919) % Eligible.Num());
        for (int32 SampleIndex = 0;
             SampleIndex < DesiredReserveCount;
             ++SampleIndex)
        {
            const int32 CandidateIndex = Eligible.IsEmpty()
                ? INDEX_NONE
                : (StableOffset + static_cast<int32>(
                    (static_cast<int64>(SampleIndex) * Eligible.Num()) /
                    FMath::Max(DesiredReserveCount, 1))) % Eligible.Num();
            if (!Eligible.IsValidIndex(CandidateIndex))
            {
                return false;
            }
            const FCentralSpawnSlot& Candidate = Eligible[CandidateIndex];
            if (Candidate.LaneIndex == Original.LaneIndex &&
                FMath::IsNearlyEqual(
                    Candidate.DistanceAlongLane,
                    Original.DistanceAlongLane,
                    0.01f))
            {
                continue;
            }
            const bool bDuplicate = Reserves.ContainsByPredicate(
                [&Candidate](const FCentralSpawnSlot& Existing)
                {
                    return Existing.LaneIndex == Candidate.LaneIndex &&
                        FMath::IsNearlyEqual(
                            Existing.DistanceAlongLane,
                            Candidate.DistanceAlongLane,
                            0.01f);
                });
            if (!bDuplicate)
            {
                Reserves.Add(Candidate);
            }
        }
        if (!Reserves.IsEmpty())
        {
            ++PlanSlotsWithReserves;
        }
        CentralReserveSpawnSlotCount += Reserves.Num();
        PlanScopedReserveCountsByDistrict[DistrictIndex] += Reserves.Num();
    }
    if (CentralSpawnLanePlan.Num() != CentralAdmissionTargetCount ||
        CentralSpawnDistancePlan.Num() != CentralAdmissionTargetCount ||
        CentralSpawnPositionPlan.Num() != CentralAdmissionTargetCount ||
        CentralSpawnDistrictPlan.Num() != CentralAdmissionTargetCount)
    {
        return false;
    }

    UE_LOG(
        LogTemp,
        Warning,
        TEXT("OPEN_MASS_CROWD_CENTRAL_SPAWN_PLAN gate=%d planned=%d full_slots=%d full_district_quotas=51,51,47,51,50,50 minimum_clearance_cm=%.3f certified_tracks=true strict_lane_interior=true expanded_local_interval_free=true whole_opposing_free=true cell_geographic_pools=true stable_full_prefix=true"),
        CentralAdmissionTargetCount,
        CentralSpawnLanePlan.Num(),
        PlannedSpawnPositions.Num(),
        MinimumPlannedSpawnClearanceCm);
    UE_LOG(
        LogTemp,
        Warning,
        TEXT("OPEN_MASS_CROWD_CENTRAL_RESERVE_PLAN gate=%d plan_slots_with_reserve=%d plan_scoped_reserves=%d per_district=%d,%d,%d,%d,%d,%d unique_usable_per_district=%d,%d,%d,%d,%d,%d max_per_plan=%d same_district=true exact_runtime_lanes=true incremental_live_plan=true bounded_live_probe_budget=%d"),
        CentralAdmissionTargetCount,
        PlanSlotsWithReserves,
        CentralReserveSpawnSlotCount,
        PlanScopedReserveCountsByDistrict[0],
        PlanScopedReserveCountsByDistrict[1],
        PlanScopedReserveCountsByDistrict[2],
        PlanScopedReserveCountsByDistrict[3],
        PlanScopedReserveCountsByDistrict[4],
        PlanScopedReserveCountsByDistrict[5],
        UniqueUsableReserveCountsByDistrict[0],
        UniqueUsableReserveCountsByDistrict[1],
        UniqueUsableReserveCountsByDistrict[2],
        UniqueUsableReserveCountsByDistrict[3],
        UniqueUsableReserveCountsByDistrict[4],
        UniqueUsableReserveCountsByDistrict[5],
        CentralMaximumReserveSlotsPerPlanIndex,
        CentralAdmissionLiveProbeBudgetPerPass);
    EntityRouteStates.Reset();
    EntityRouteStates.SetNum(CentralAdmissionTargetCount);
    CentralPresentationOffsetValid.Init(0, CentralAdmissionTargetCount);
    LastValidGroundStates.Reset();
    LastValidGroundStates.SetNum(CentralAdmissionTargetCount);
    CentralAdmittedEntityCount = 0;
    bCentralAdmissionReleased = false;
    CentralSimulatedEntityCount = 0;
    CentralRepresentedEntityCount = 0;
    CentralAdmissionBatchCount = 0;
    CentralMaximumCommittedAdmissionBatchSize = 0;
    CentralAdmissionLiveGroundProbeCount = 0;
    CentralAdmissionLiveGroundRejectCount = 0;
    CentralAdmissionNoRawSupportCount = 0;
    CentralReserveReplacementCount = 0;
    CentralReserveCycleDeferCount = 0;
    LastLoggedCentralRepresentedCount = INDEX_NONE;
    ResetCentralRuntimeTelemetry();
    CentralPlannedSpawnSlotCount = PlannedSpawnPositions.Num();
    CentralMinimumPlannedSpawnClearanceCm =
        MinimumPlannedSpawnClearanceCm;
    RouteAssignmentCount = 0;
    CompletedTripCount = 0;
    CentralShortPathChunkCount = 0;
    RouteReplanCount = 0;
    GroundProjectionFailureCount = 0;
    GroundRollbackCount = 0;
    GroundCenterRecoveryCount = 0;
    GroundUnrecoverableCount = 0;
    CurrentUnsupportedVisualCount = 0;
    MaxConsecutiveGroundMisses = 0;
    const int32 RouteSeed =
        CentralAdmissionTargetCount * 7919 ^
        FMath::RoundToInt(GetActorLocation().X) * 31 ^
        FMath::RoundToInt(GetActorLocation().Y) * 17;
    RouteRandomStream.Initialize(RouteSeed);

    return AdmitNextCentralBatch();
}

bool AOpenMassCrowdSpawner::AdmitNextCentralBatch()
{
    UMassSpawnerSubsystem* SpawnerSubsystem =
        UWorld::GetSubsystem<UMassSpawnerSubsystem>(GetWorld());
    if (!SpawnerSubsystem ||
        CentralAdmissionTargetCount <= 0 ||
        SpawnedEntities.Num() >= CentralAdmissionTargetCount)
    {
        return SpawnedEntities.Num() == CentralAdmissionTargetCount;
    }

    int32 BatchCount = FMath::Min(
        FMath::Clamp(CentralAdmissionBatchSize, 1, 50),
        CentralAdmissionTargetCount - SpawnedEntities.Num());
    const int32 FirstEntityIndex = SpawnedEntities.Num();
    FMassEntityManager& EntityManager =
        SpawnerSubsystem->GetEntityManagerChecked();
    struct FCentralAdmissionOccupant
    {
        FVector Position = FVector::ZeroVector;
        int32 LaneIndex = INDEX_NONE;
        float DistanceAlongLaneCm = 0.0f;
        TArray<uint8> DeclaredLocalConflictSides;
    };
    struct FCentralStagedAdmissionSlot
    {
        int32 PlanIndex = INDEX_NONE;
        int32 DistrictIndex = INDEX_NONE;
        FCentralSpawnSlot CachedSlot;
        FVector LivePosition = FVector::ZeroVector;
        float MinimumClearanceSquared = TNumericLimits<float>::Max();
        int32 ReserveIndex = INDEX_NONE;
        int32 PreviousLaneIndex = INDEX_NONE;
    };
    TArray<FCentralAdmissionOccupant> Occupants;
    Occupants.Reserve(SpawnedEntities.Num() + BatchCount);
    for (int32 ExistingEntityIndex = 0;
         ExistingEntityIndex < SpawnedEntities.Num();
         ++ExistingEntityIndex)
    {
        const FMassEntityHandle ExistingEntity =
            SpawnedEntities[ExistingEntityIndex];
        if (!EntityManager.IsEntityValid(ExistingEntity))
        {
            UE_LOG(
                LogTemp,
                Warning,
                TEXT("OPEN_MASS_CROWD_CENTRAL_ADMISSION_DEFER admitted=%d target=%d retry_s=%.3f reason=invalid_existing_entity_fail_closed"),
                SpawnedEntities.Num(),
                CentralAdmissionTargetCount,
                FMath::Max(CentralAdmissionBatchInterval, 0.01f));
            GetWorldTimerManager().SetTimer(
                CentralAdmissionTimer,
                this,
                &AOpenMassCrowdSpawner::ContinueCentralAdmission,
                FMath::Max(CentralAdmissionBatchInterval, 0.01f),
                false);
            return true;
        }
        // Admission is frozen until every entity has committed. Use the exact
        // live-certified transaction records as occupancy authority instead of
        // a Mass transform that initialization processors may have adjusted by
        // a few centimetres. The complete 100-slot plan already validates these
        // same positions together, so both the structural and dynamic checks
        // now evaluate one coordinate system.
        if (!CentralSpawnLivePositionPlan.IsValidIndex(ExistingEntityIndex) ||
            !CentralSpawnLivePositionValid.IsValidIndex(ExistingEntityIndex) ||
            CentralSpawnLivePositionValid[ExistingEntityIndex] == 0 ||
            !CentralSpawnLanePlan.IsValidIndex(ExistingEntityIndex) ||
            !CentralSpawnDistancePlan.IsValidIndex(ExistingEntityIndex))
        {
            UE_LOG(
                LogTemp,
                Warning,
                TEXT("OPEN_MASS_CROWD_CENTRAL_ADMISSION_DEFER admitted=%d target=%d retry_s=%.3f reason=existing_entity_missing_certified_transaction_state"),
                SpawnedEntities.Num(),
                CentralAdmissionTargetCount,
                FMath::Max(CentralAdmissionBatchInterval, 0.01f));
            GetWorldTimerManager().SetTimer(
                CentralAdmissionTimer,
                this,
                &AOpenMassCrowdSpawner::ContinueCentralAdmission,
                FMath::Max(CentralAdmissionBatchInterval, 0.01f),
                false);
            return true;
        }
        const FVector ExistingPosition =
            CentralSpawnLivePositionPlan[ExistingEntityIndex];
        const int32 ExistingLaneIndex =
            CentralSpawnLanePlan[ExistingEntityIndex];
        const float ExistingDistanceAlongLane =
            CentralSpawnDistancePlan[ExistingEntityIndex];
        if (ExistingPosition.ContainsNaN() ||
            !FMath::IsFinite(ExistingPosition.X) ||
            !FMath::IsFinite(ExistingPosition.Y) ||
            !FMath::IsFinite(ExistingPosition.Z) ||
            !FMath::IsFinite(ExistingDistanceAlongLane) ||
            !RuntimeLaneHandles.IsValidIndex(ExistingLaneIndex) ||
            !RuntimeCentralOpposingPhysicalLaneIndices.IsValidIndex(
                ExistingLaneIndex) ||
            !RuntimeCentralLocalConflictIndicesByLane.IsValidIndex(
                ExistingLaneIndex))
        {
            UE_LOG(
                LogTemp,
                Warning,
                TEXT("OPEN_MASS_CROWD_CENTRAL_ADMISSION_DEFER admitted=%d target=%d retry_s=%.3f reason=invalid_existing_clearance_state_fail_closed"),
                SpawnedEntities.Num(),
                CentralAdmissionTargetCount,
                FMath::Max(CentralAdmissionBatchInterval, 0.01f));
            GetWorldTimerManager().SetTimer(
                CentralAdmissionTimer,
                this,
                &AOpenMassCrowdSpawner::ContinueCentralAdmission,
                FMath::Max(CentralAdmissionBatchInterval, 0.01f),
                false);
            return true;
        }
        FCentralAdmissionOccupant& Occupant =
            Occupants.AddDefaulted_GetRef();
        Occupant.Position = ExistingPosition;
        Occupant.LaneIndex = ExistingLaneIndex;
        Occupant.DistanceAlongLaneCm = ExistingDistanceAlongLane;
    }

    const auto IsDistanceInsideConflictInterval = [](
        const FRuntimeCentralLocalConflict& Conflict,
        const int32 LaneIndex,
        const float DistanceAlongLaneCm)
    {
        if (LaneIndex == Conflict.FirstLaneIndex)
        {
            return DistanceAlongLaneCm >=
                    FMath::Max(
                        0.0f,
                        Conflict.FirstBeginDistanceCm -
                            CentralMinimumCenterClearanceCm) &&
                DistanceAlongLaneCm <=
                    Conflict.FirstEndDistanceCm +
                        CentralMinimumCenterClearanceCm;
        }
        if (LaneIndex == Conflict.SecondLaneIndex)
        {
            return DistanceAlongLaneCm >=
                    FMath::Max(
                        0.0f,
                        Conflict.SecondBeginDistanceCm -
                            CentralMinimumCenterClearanceCm) &&
                DistanceAlongLaneCm <=
                    Conflict.SecondEndDistanceCm +
                        CentralMinimumCenterClearanceCm;
        }
        return false;
    };
    const auto BuildLocalConflictSideDeclaration = [
        this,
        &IsDistanceInsideConflictInterval](
            const int32 LaneIndex,
            const float DistanceAlongLaneCm,
            TArray<uint8>& OutDeclaredSides,
            TArray<int32>* OutDeclaredConflictIndices)
    {
        OutDeclaredSides.SetNumZeroed(RuntimeCentralLocalConflicts.Num());
        if (OutDeclaredConflictIndices)
        {
            OutDeclaredConflictIndices->Reset();
        }
        if (!RuntimeCentralLocalConflictIndicesByLane.IsValidIndex(LaneIndex) ||
            !RuntimeCentralLocalConflictClosuresByLane.IsValidIndex(LaneIndex))
        {
            return false;
        }
        for (const int32 SeedConflictIndex :
             RuntimeCentralLocalConflictIndicesByLane[LaneIndex])
        {
            if (!RuntimeCentralLocalConflicts.IsValidIndex(
                    SeedConflictIndex))
            {
                return false;
            }
            const FRuntimeCentralLocalConflict& SeedConflict =
                RuntimeCentralLocalConflicts[SeedConflictIndex];
            if (SeedConflict.bRuntimeOnlyReservation)
            {
                continue;
            }
            if (!IsDistanceInsideConflictInterval(
                    SeedConflict,
                    LaneIndex,
                    DistanceAlongLaneCm))
            {
                continue;
            }
            const TArray<int32>* ClosureConflictIndices =
                RuntimeCentralLocalConflictClosuresByLane[LaneIndex].Find(
                    SeedConflictIndex);
            if (!ClosureConflictIndices)
            {
                return false;
            }
            for (const int32 ClosureConflictIndex :
                 *ClosureConflictIndices)
            {
                if (!RuntimeCentralLocalConflicts.IsValidIndex(
                        ClosureConflictIndex) ||
                    !OutDeclaredSides.IsValidIndex(ClosureConflictIndex))
                {
                    return false;
                }
                const FRuntimeCentralLocalConflict& ClosureConflict =
                    RuntimeCentralLocalConflicts[ClosureConflictIndex];
                if (ClosureConflict.bRuntimeOnlyReservation)
                {
                    continue;
                }
                uint8 SideMask = 0;
                if (LaneIndex == ClosureConflict.FirstLaneIndex)
                {
                    SideMask = 1;
                }
                else if (LaneIndex == ClosureConflict.SecondLaneIndex)
                {
                    SideMask = 2;
                }
                else
                {
                    return false;
                }
                if (OutDeclaredSides[ClosureConflictIndex] == 0 &&
                    OutDeclaredConflictIndices)
                {
                    OutDeclaredConflictIndices->Add(ClosureConflictIndex);
                }
                OutDeclaredSides[ClosureConflictIndex] |= SideMask;
            }
        }
        return true;
    };
    for (FCentralAdmissionOccupant& Occupant : Occupants)
    {
        if (!BuildLocalConflictSideDeclaration(
                Occupant.LaneIndex,
                Occupant.DistanceAlongLaneCm,
                Occupant.DeclaredLocalConflictSides,
                nullptr))
        {
            UE_LOG(
                LogTemp,
                Warning,
                TEXT("OPEN_MASS_CROWD_CENTRAL_ADMISSION_DEFER admitted=%d target=%d retry_s=%.3f reason=invalid_existing_conflict_declaration"),
                SpawnedEntities.Num(),
                CentralAdmissionTargetCount,
                FMath::Max(CentralAdmissionBatchInterval, 0.01f));
            GetWorldTimerManager().SetTimer(
                CentralAdmissionTimer,
                this,
                &AOpenMassCrowdSpawner::ContinueCentralAdmission,
                FMath::Max(CentralAdmissionBatchInterval, 0.01f),
                false);
            return true;
        }
    }
    const float RequiredSpawnClearanceSquared =
        FMath::Square(
            CentralMinimumAcceptedInitialPackingClearanceCm);
    int32 RemainingLiveProbeBudget =
        CentralAdmissionLiveProbeBudgetPerPass;
    TArray<FCentralStagedAdmissionSlot> StagedSlots;
    StagedSlots.Reserve(BatchCount);

    const auto AreOpposingLanes = [this](
        const int32 FirstLaneIndex,
        const int32 SecondLaneIndex)
    {
        return !RuntimeCentralOpposingPhysicalLaneIndices.IsValidIndex(
                    FirstLaneIndex) ||
                !RuntimeCentralOpposingPhysicalLaneIndices.IsValidIndex(
                    SecondLaneIndex) ||
            RuntimeCentralOpposingPhysicalLaneIndices[
                FirstLaneIndex].Contains(SecondLaneIndex) ||
            RuntimeCentralOpposingPhysicalLaneIndices[
                SecondLaneIndex].Contains(FirstLaneIndex);
    };

    const auto GetPlannedLaneAndPosition = [
        this,
        &StagedSlots](
            const int32 PlanIndex,
            int32& OutLaneIndex,
            FVector& OutPosition)
    {
        for (const FCentralStagedAdmissionSlot& Staged : StagedSlots)
        {
            if (Staged.PlanIndex == PlanIndex)
            {
                OutLaneIndex = Staged.CachedSlot.LaneIndex;
                OutPosition = Staged.LivePosition;
                return true;
            }
        }
        if (!CentralSpawnLanePlan.IsValidIndex(PlanIndex) ||
            !CentralSpawnPositionPlan.IsValidIndex(PlanIndex))
        {
            return false;
        }
        OutLaneIndex = CentralSpawnLanePlan[PlanIndex];
        OutPosition = CentralSpawnPositionPlan[PlanIndex];
        return true;
    };

    const auto IsAdmissionCandidateStructurallySafe = [
        this,
        &BuildLocalConflictSideDeclaration,
        &GetPlannedLaneAndPosition,
        &AreOpposingLanes,
        RequiredSpawnClearanceSquared](
            const int32 TargetPlanIndex,
            const FCentralSpawnSlot& Candidate,
            const FVector& CandidateLivePosition,
            float& OutMinimumClearanceSquared)
    {
        OutMinimumClearanceSquared = TNumericLimits<float>::Max();
        if (!RuntimeCentralOpposingPhysicalLaneIndices.IsValidIndex(
                Candidate.LaneIndex) ||
            Candidate.Position.ContainsNaN() ||
            CandidateLivePosition.ContainsNaN() ||
            !FMath::IsFinite(Candidate.DistanceAlongLane))
        {
            return false;
        }
        TArray<uint8> CandidateDeclaredSides;
        TArray<int32> CandidateDeclaredConflictIndices;
        if (!BuildLocalConflictSideDeclaration(
                Candidate.LaneIndex,
                Candidate.DistanceAlongLane,
                CandidateDeclaredSides,
                &CandidateDeclaredConflictIndices) ||
            !CandidateDeclaredConflictIndices.IsEmpty())
        {
            // Admission never starts inside a body-expanded local resource.
            return false;
        }
        // Future cached slots are proposals, not occupancy. Validate only the
        // already committed/staged prefix so a stale future Cesium sample cannot
        // prevent this index from taking a live-safe reserve. The completed
        // prefix therefore becomes the authoritative runtime plan incrementally.
        for (int32 OtherPlanIndex = 0;
             OtherPlanIndex < TargetPlanIndex;
             ++OtherPlanIndex)
        {
            if (OtherPlanIndex == TargetPlanIndex)
            {
                continue;
            }
            int32 OtherLaneIndex = INDEX_NONE;
            FVector OtherPosition;
            if (!GetPlannedLaneAndPosition(
                    OtherPlanIndex,
                    OtherLaneIndex,
                    OtherPosition))
            {
                return false;
            }
            // XY separation is authoritative for upright bodies. A large Z
            // mismatch may never disguise the same pavement footprint.
            const float DistanceSquared = FVector::DistSquared2D(
                CandidateLivePosition,
                OtherPosition);
            OutMinimumClearanceSquared = FMath::Min(
                OutMinimumClearanceSquared,
                DistanceSquared);
            const bool bOpposing = AreOpposingLanes(
                Candidate.LaneIndex,
                OtherLaneIndex);
            if (DistanceSquared < RequiredSpawnClearanceSquared || bOpposing)
            {
                return false;
            }
        }
        return true;
    };

    const auto IsAdmissionCandidateDynamicallySafe = [
        this,
        &Occupants,
        &AreOpposingLanes,
        RequiredSpawnClearanceSquared](
            const FCentralSpawnSlot& Candidate,
            const FVector& CandidateLivePosition,
            float& InOutMinimumClearanceSquared)
    {
        if (!RuntimeCentralReverseLaneIndices.IsValidIndex(
                Candidate.LaneIndex))
        {
            return false;
        }
        const int32 CandidateReverseLaneIndex =
            RuntimeCentralReverseLaneIndices[Candidate.LaneIndex];
        const int32 CandidatePairKey = FMath::Min(
            Candidate.LaneIndex,
            CandidateReverseLaneIndex);
        int32 ExistingPairUseCount = 0;
        for (const FCentralAdmissionOccupant& Occupant : Occupants)
        {
            if (!RuntimeCentralReverseLaneIndices.IsValidIndex(
                    Occupant.LaneIndex))
            {
                return false;
            }
            const int32 OccupantPairKey = FMath::Min(
                Occupant.LaneIndex,
                RuntimeCentralReverseLaneIndices[Occupant.LaneIndex]);
            if (OccupantPairKey == CandidatePairKey)
            {
                // A loop admits at most one same-direction pair. Edge-circulation
                // entities use one shared cruise speed below, preserving their
                // certified initial headway instead of letting a faster follower
                // catch and deadlock behind a slower leader.
                if (Occupant.LaneIndex != Candidate.LaneIndex ||
                    ++ExistingPairUseCount >=
                        CentralMaximumPedestriansPerEdgeCirculation)
                {
                    return false;
                }
            }
            const float DistanceSquared = FVector::DistSquared2D(
                CandidateLivePosition,
                Occupant.Position);
            InOutMinimumClearanceSquared = FMath::Min(
                InOutMinimumClearanceSquared,
                DistanceSquared);
            if (DistanceSquared < RequiredSpawnClearanceSquared ||
                AreOpposingLanes(
                    Candidate.LaneIndex,
                    Occupant.LaneIndex))
            {
                return false;
            }
        }
        return true;
    };

    enum class ECentralLiveProbeResult : uint8
    {
        Accepted,
        BudgetExhausted,
        TransientNoRawSupport,
        StructuralReject
    };
    const auto ProbeLiveCertifiedSlot = [
        this,
        &RemainingLiveProbeBudget](
            const FCentralSpawnSlot& Candidate,
            FVector& OutLivePosition)
    {
        if (RemainingLiveProbeBudget <= 0)
        {
            return ECentralLiveProbeResult::BudgetExhausted;
        }
        --RemainingLiveProbeBudget;
        ++CentralAdmissionLiveGroundProbeCount;
        ++CentralGroundGuardQueryCount;
        FVector GroundPoint = FVector::ZeroVector;
        const ECesiumGroundProjectionResult ProjectionResult =
            ProjectToCesiumGroundClassified(
                Candidate.Position,
                GroundPoint);
        OutLivePosition = GroundPoint + FVector(0.0, 0.0, LaneHeightOffset);
        if (ProjectionResult ==
            ECesiumGroundProjectionResult::NoRawSupport)
        {
            ++CentralAdmissionLiveGroundRejectCount;
            ++CentralAdmissionNoRawSupportCount;
            return ECentralLiveProbeResult::TransientNoRawSupport;
        }
        if (ProjectionResult !=
            ECesiumGroundProjectionResult::Accepted)
        {
            ++CentralAdmissionLiveGroundRejectCount;
            return ECentralLiveProbeResult::StructuralReject;
        }
        // Preserve exact XY and Initialize's original cache tolerance. The raw
        // first blocker, Cesium ownership and slope rules remain exclusively
        // owned by ProjectToCesiumGround above.
        if (FVector::DistSquared2D(
                OutLivePosition,
                Candidate.Position) > 0.01f ||
            FMath::Square(
                OutLivePosition.Z - Candidate.Position.Z) >
                FMath::Square(GroundTolerance))
        {
            ++CentralAdmissionLiveGroundRejectCount;
            return ECentralLiveProbeResult::StructuralReject;
        }
        return ECentralLiveProbeResult::Accepted;
    };

    enum class ECentralAdmissionStageResult : uint8
    {
        Success,
        DynamicDefer,
        ProbeBudgetDefer,
        ReserveCycleDefer,
        Invalid
    };
    const auto TryStageAdmissionSlot = [
        this,
        &StagedSlots,
        &IsAdmissionCandidateStructurallySafe,
        &IsAdmissionCandidateDynamicallySafe,
        &ProbeLiveCertifiedSlot,
        &RemainingLiveProbeBudget](const int32 TargetPlanIndex)
    {
        if (!CentralSpawnLanePlan.IsValidIndex(TargetPlanIndex) ||
            !CentralSpawnDistancePlan.IsValidIndex(TargetPlanIndex) ||
            !CentralSpawnPositionPlan.IsValidIndex(TargetPlanIndex) ||
            !CentralSpawnDistrictPlan.IsValidIndex(TargetPlanIndex) ||
            !CentralReserveSpawnSlotsByPlanIndex.IsValidIndex(
                TargetPlanIndex) ||
            !CentralReserveSpawnCursorsByPlanIndex.IsValidIndex(
                TargetPlanIndex))
        {
            return ECentralAdmissionStageResult::Invalid;
        }
        const int32 DistrictIndex =
            CentralSpawnDistrictPlan[TargetPlanIndex];
        if (!RuntimeCentralDistricts.IsValidIndex(DistrictIndex))
        {
            return ECentralAdmissionStageResult::Invalid;
        }

        FCentralSpawnSlot Original;
        Original.LaneIndex = CentralSpawnLanePlan[TargetPlanIndex];
        Original.DistanceAlongLane =
            CentralSpawnDistancePlan[TargetPlanIndex];
        Original.Position = CentralSpawnPositionPlan[TargetPlanIndex];
        FVector OriginalLivePosition = FVector::ZeroVector;
        if (RemainingLiveProbeBudget <= 0)
        {
            return ECentralAdmissionStageResult::ProbeBudgetDefer;
        }
        const ECentralLiveProbeResult OriginalProbeResult =
            ProbeLiveCertifiedSlot(Original, OriginalLivePosition);
        if (OriginalProbeResult ==
            ECentralLiveProbeResult::BudgetExhausted)
        {
            return ECentralAdmissionStageResult::ProbeBudgetDefer;
        }
        // A no-hit original can be a transient tile gap or a persistently
        // absent collision triangle. Reprobe the immutable original next pass,
        // but let this pass try same-district reserves so one bad XY cannot
        // deadlock admission forever.
        if (OriginalProbeResult == ECentralLiveProbeResult::Accepted)
        {
            float MinimumClearanceSquared =
                TNumericLimits<float>::Max();
            if (IsAdmissionCandidateStructurallySafe(
                    TargetPlanIndex,
                    Original,
                    OriginalLivePosition,
                    MinimumClearanceSquared))
            {
                if (!IsAdmissionCandidateDynamicallySafe(
                        Original,
                        OriginalLivePosition,
                        MinimumClearanceSquared))
                {
                    // The immutable original is temporarily occupied. Continue
                    // through this plan index's deterministic reserve list;
                    // repeatedly probing only the original can pin admission
                    // forever when an entity is held at that exact node.
                }
                else
                {
                    FCentralStagedAdmissionSlot& Staged =
                        StagedSlots.AddDefaulted_GetRef();
                    Staged.PlanIndex = TargetPlanIndex;
                    Staged.DistrictIndex = DistrictIndex;
                    Staged.CachedSlot = Original;
                    Staged.LivePosition = OriginalLivePosition;
                    Staged.MinimumClearanceSquared =
                        MinimumClearanceSquared;
                    Staged.PreviousLaneIndex = Original.LaneIndex;
                    return ECentralAdmissionStageResult::Success;
                }
            }
        }

        TArray<FCentralSpawnSlot>& Reserves =
            CentralReserveSpawnSlotsByPlanIndex[TargetPlanIndex];
        int32& ReserveCursor =
            CentralReserveSpawnCursorsByPlanIndex[TargetPlanIndex];
        if (Reserves.IsEmpty())
        {
            CentralReserveCycleDeferCount =
                CentralReserveCycleDeferCount >= MAX_int32
                ? MAX_int32
                : CentralReserveCycleDeferCount + 1;
            UE_LOG(
                LogTemp,
                Warning,
                TEXT("OPEN_MASS_CROWD_CENTRAL_RESERVE_DEFER entity=%d district=%s reserve_count=0 cursor=0 reason=no_certified_reserve fail_closed=true retryable=true"),
                TargetPlanIndex,
                *RuntimeCentralDistricts[
                    DistrictIndex].DistrictId.ToString());
            return ECentralAdmissionStageResult::ReserveCycleDefer;
        }
        if (ReserveCursor < 0)
        {
            return ECentralAdmissionStageResult::Invalid;
        }
        ReserveCursor %= Reserves.Num();
        int32 ReserveAttemptsThisPass = 0;
        const auto AdvanceReserveCursor = [
            &ReserveCursor,
            &ReserveAttemptsThisPass,
            &Reserves]()
        {
            ReserveCursor = (ReserveCursor + 1) % Reserves.Num();
            ++ReserveAttemptsThisPass;
        };
        while (ReserveAttemptsThisPass < Reserves.Num())
        {
            const int32 CandidateReserveIndex = ReserveCursor;
            const FCentralSpawnSlot& Candidate =
                Reserves[CandidateReserveIndex];
            if (!RuntimeCentralDistricts[DistrictIndex].SpawnLaneIndices.Contains(
                    Candidate.LaneIndex))
            {
                return ECentralAdmissionStageResult::Invalid;
            }
            // XY, lane identity and occupancy are already known before a live
            // trace. Reject used edge loops and insufficient-clearance samples
            // first so the bounded Cesium budget is spent only on candidates
            // that can actually join the runtime plan.
            float CandidateMinimumClearanceSquared =
                TNumericLimits<float>::Max();
            if (!IsAdmissionCandidateStructurallySafe(
                    TargetPlanIndex,
                    Candidate,
                    Candidate.Position,
                    CandidateMinimumClearanceSquared) ||
                !IsAdmissionCandidateDynamicallySafe(
                    Candidate,
                    Candidate.Position,
                    CandidateMinimumClearanceSquared))
            {
                AdvanceReserveCursor();
                continue;
            }
            if (RemainingLiveProbeBudget <= 0)
            {
                return ECentralAdmissionStageResult::ProbeBudgetDefer;
            }
            FVector CandidateLivePosition = FVector::ZeroVector;
            const ECentralLiveProbeResult CandidateProbeResult =
                ProbeLiveCertifiedSlot(
                    Candidate,
                    CandidateLivePosition);
            if (CandidateProbeResult ==
                ECentralLiveProbeResult::BudgetExhausted)
            {
                return ECentralAdmissionStageResult::ProbeBudgetDefer;
            }
            if (CandidateProbeResult ==
                ECentralLiveProbeResult::TransientNoRawSupport)
            {
                // Missing support can recover after Cesium LOD refinement. Move
                // the rotation start so this pass still makes bounded progress,
                // but wrap on a later pass instead of permanently consuming it.
                AdvanceReserveCursor();
                continue;
            }
            if (CandidateProbeResult ==
                ECentralLiveProbeResult::StructuralReject)
            {
                // Even ownership/normal/elevation can change with streamed LOD.
                // Rotate rather than permanently burning the candidate.
                AdvanceReserveCursor();
                continue;
            }
            CandidateMinimumClearanceSquared = TNumericLimits<float>::Max();
            if (!IsAdmissionCandidateStructurallySafe(
                    TargetPlanIndex,
                    Candidate,
                    CandidateLivePosition,
                    CandidateMinimumClearanceSquared))
            {
                // A conflict with another staged replacement may disappear if
                // streaming changes that earlier choice on the next pass.
                AdvanceReserveCursor();
                continue;
            }
            if (!IsAdmissionCandidateDynamicallySafe(
                    Candidate,
                    CandidateLivePosition,
                    CandidateMinimumClearanceSquared))
            {
                // This live-valid reserve is occupied now, but another reserve
                // in the same certified district can still be safe. Complete
                // one bounded rotation before deferring the plan index.
                AdvanceReserveCursor();
                continue;
            }
            FCentralStagedAdmissionSlot& Staged =
                StagedSlots.AddDefaulted_GetRef();
            Staged.PlanIndex = TargetPlanIndex;
            Staged.DistrictIndex = DistrictIndex;
            Staged.CachedSlot = Candidate;
            Staged.LivePosition = CandidateLivePosition;
            Staged.MinimumClearanceSquared =
                CandidateMinimumClearanceSquared;
            Staged.ReserveIndex = CandidateReserveIndex;
            Staged.PreviousLaneIndex = Original.LaneIndex;
            return ECentralAdmissionStageResult::Success;
        }

        CentralReserveCycleDeferCount =
            CentralReserveCycleDeferCount >= MAX_int32
            ? MAX_int32
            : CentralReserveCycleDeferCount + 1;
        UE_LOG(
            LogTemp,
            Warning,
            TEXT("OPEN_MASS_CROWD_CENTRAL_RESERVE_DEFER entity=%d district=%s reserve_count=%d cursor=%d attempts=%d reason=full_rotation_without_live_safe_candidate fail_closed=true retryable=true"),
            TargetPlanIndex,
            *RuntimeCentralDistricts[DistrictIndex].DistrictId.ToString(),
            Reserves.Num(),
            ReserveCursor,
            ReserveAttemptsThisPass);
        return ECentralAdmissionStageResult::ReserveCycleDefer;
    };

    ECentralAdmissionStageResult StageResult =
        ECentralAdmissionStageResult::Success;
    for (int32 BatchOffset = 0; BatchOffset < BatchCount; ++BatchOffset)
    {
        const int32 TargetPlanIndex = FirstEntityIndex + BatchOffset;
        StageResult = TryStageAdmissionSlot(TargetPlanIndex);
        if (StageResult != ECentralAdmissionStageResult::Success ||
            StagedSlots.Num() != BatchOffset + 1)
        {
            break;
        }
        const FCentralStagedAdmissionSlot& Staged = StagedSlots.Last();
        FCentralAdmissionOccupant& Selected =
            Occupants.AddDefaulted_GetRef();
        Selected.Position = Staged.LivePosition;
        Selected.LaneIndex = Staged.CachedSlot.LaneIndex;
        Selected.DistanceAlongLaneCm =
            Staged.CachedSlot.DistanceAlongLane;
        if (!BuildLocalConflictSideDeclaration(
                Selected.LaneIndex,
                Selected.DistanceAlongLaneCm,
                Selected.DeclaredLocalConflictSides,
                nullptr))
        {
            Occupants.Pop();
            StagedSlots.Pop();
            StageResult = ECentralAdmissionStageResult::Invalid;
            break;
        }
    }
    const ECentralAdmissionStageResult TruncatedStageResult = StageResult;
    if ((StageResult == ECentralAdmissionStageResult::ProbeBudgetDefer ||
         StageResult == ECentralAdmissionStageResult::DynamicDefer ||
         StageResult == ECentralAdmissionStageResult::ReserveCycleDefer) &&
        !StagedSlots.IsEmpty())
    {
        // The prefix is already a complete deterministic transaction. Commit
        // it as a smaller bounded batch instead of reprobing the same prefix on
        // every retry. This applies both when the live-probe budget ends and
        // when the first unsafe slot is only transiently occupied by an already
        // moving pedestrian. The blocked slot stays at the head of the next
        // transaction; stable plan order and all live clearance checks remain
        // unchanged.
        BatchCount = StagedSlots.Num();
        StageResult = ECentralAdmissionStageResult::Success;
        UE_LOG(
            LogTemp,
            Warning,
            TEXT("OPEN_MASS_CROWD_CENTRAL_ADMISSION_BATCH_TRUNCATED admitted=%d committed_prefix=%d configured_batch=%d reason=%s stable_order=true transactional=true blocked_slot_retried_next=true"),
            SpawnedEntities.Num(),
            BatchCount,
            FMath::Clamp(CentralAdmissionBatchSize, 1, 50),
            TruncatedStageResult ==
                    ECentralAdmissionStageResult::ProbeBudgetDefer
                ? TEXT("live_probe_budget")
                : TruncatedStageResult ==
                        ECentralAdmissionStageResult::ReserveCycleDefer
                    ? TEXT("reserve_cycle_exhausted")
                    : TEXT("transient_dynamic_occupancy"));
    }
    if (StageResult != ECentralAdmissionStageResult::Success ||
        StagedSlots.Num() != BatchCount)
    {
        const TCHAR* DeferReason = TEXT("invalid");
        switch (StageResult)
        {
        case ECentralAdmissionStageResult::DynamicDefer:
            DeferReason = TEXT("transient_dynamic_occupancy");
            break;
        case ECentralAdmissionStageResult::ProbeBudgetDefer:
            DeferReason = TEXT("bounded_live_probe_budget");
            break;
        case ECentralAdmissionStageResult::ReserveCycleDefer:
            DeferReason = TEXT("reserve_rotation_without_live_safe_candidate");
            break;
        default:
            break;
        }
        UE_LOG(
            LogTemp,
            Warning,
            TEXT("OPEN_MASS_CROWD_CENTRAL_ADMISSION_DEFER admitted=%d target=%d requested_batch=%d staged_slots=%d retry_s=%.3f reason=%s stable_plan_prefix=true transactional_staging=true same_district_reserves=true remaining_probe_budget=%d live_probes=%d live_rejects=%d no_raw_support_observations=%d committed_replacements=%d reserve_cycle_defers=%d"),
            SpawnedEntities.Num(),
            CentralAdmissionTargetCount,
            BatchCount,
            StagedSlots.Num(),
            FMath::Max(CentralAdmissionBatchInterval, 0.01f),
            DeferReason,
            RemainingLiveProbeBudget,
            CentralAdmissionLiveGroundProbeCount,
            CentralAdmissionLiveGroundRejectCount,
            CentralAdmissionNoRawSupportCount,
            CentralReserveReplacementCount,
            CentralReserveCycleDeferCount);
        GetWorldTimerManager().SetTimer(
            CentralAdmissionTimer,
            this,
            &AOpenMassCrowdSpawner::ContinueCentralAdmission,
            FMath::Max(CentralAdmissionBatchInterval, 0.01f),
            false);
        return true;
    }

    // Validate variant capacity and every template against a local remaining
    // copy before committing plan/live state. A non-Mass preflight failure can
    // therefore never leave a half-committed admission transaction.
    TArray<int32> VariantBatchCounts;
    VariantBatchCounts.SetNumZeroed(CentralRuntimeTemplateIds.Num());
    TArray<int32> StagedVariantRemainingCounts =
        CentralRuntimeVariantRemainingCounts;
    if (VariantBatchCounts.IsEmpty() ||
        StagedVariantRemainingCounts.Num() != VariantBatchCounts.Num())
    {
        return false;
    }
    int32 VariantCursor =
        SpawnedEntities.Num() % CentralRuntimeTemplateIds.Num();
    for (int32 BatchOffset = 0; BatchOffset < BatchCount; ++BatchOffset)
    {
        int32 SearchCount = 0;
        while (SearchCount < StagedVariantRemainingCounts.Num() &&
            StagedVariantRemainingCounts[VariantCursor] <= 0)
        {
            VariantCursor =
                (VariantCursor + 1) % StagedVariantRemainingCounts.Num();
            ++SearchCount;
        }
        if (SearchCount == StagedVariantRemainingCounts.Num())
        {
            return false;
        }
        ++VariantBatchCounts[VariantCursor];
        --StagedVariantRemainingCounts[VariantCursor];
        VariantCursor =
            (VariantCursor + 1) % StagedVariantRemainingCounts.Num();
    }
    TArray<const FMassEntityTemplate*> ValidatedBatchTemplates;
    ValidatedBatchTemplates.SetNumZeroed(VariantBatchCounts.Num());
    for (int32 VariantIndex = 0;
         VariantIndex < VariantBatchCounts.Num();
         ++VariantIndex)
    {
        if (VariantBatchCounts[VariantIndex] <= 0)
        {
            continue;
        }
        ValidatedBatchTemplates[VariantIndex] =
            SpawnerSubsystem->GetMassEntityTemplate(
                CentralRuntimeTemplateIds[VariantIndex]);
        if (!ValidatedBatchTemplates[VariantIndex])
        {
            return false;
        }
    }

    // Commit only after the complete batch has a live exact-XY, structurally
    // safe and dynamically unoccupied slot and all non-Mass preflight passed.
    for (const FCentralStagedAdmissionSlot& Staged : StagedSlots)
    {
        const int32 PlanIndex = Staged.PlanIndex;
        if (!CentralSpawnLanePlan.IsValidIndex(PlanIndex) ||
            !CentralSpawnDistancePlan.IsValidIndex(PlanIndex) ||
            !CentralSpawnPositionPlan.IsValidIndex(PlanIndex) ||
            !CentralSpawnDistrictPlan.IsValidIndex(PlanIndex) ||
            !CentralSpawnLivePositionPlan.IsValidIndex(PlanIndex) ||
            !CentralSpawnLivePositionValid.IsValidIndex(PlanIndex) ||
            CentralSpawnDistrictPlan[PlanIndex] != Staged.DistrictIndex)
        {
            return false;
        }
    }
    for (const FCentralStagedAdmissionSlot& Staged : StagedSlots)
    {
        const int32 PlanIndex = Staged.PlanIndex;
        CentralSpawnLanePlan[PlanIndex] = Staged.CachedSlot.LaneIndex;
        CentralSpawnDistancePlan[PlanIndex] =
            Staged.CachedSlot.DistanceAlongLane;
        CentralSpawnPositionPlan[PlanIndex] =
            Staged.CachedSlot.Position;
        CentralSpawnLivePositionPlan[PlanIndex] =
            Staged.LivePosition;
        CentralSpawnLivePositionValid[PlanIndex] = 1;
    }
    CentralRuntimeVariantRemainingCounts =
        MoveTemp(StagedVariantRemainingCounts);

    for (int32 VariantIndex = 0;
         VariantIndex < VariantBatchCounts.Num();
         ++VariantIndex)
    {
        const int32 VariantCount = VariantBatchCounts[VariantIndex];
        if (VariantCount == 0)
        {
            continue;
        }
        const FMassEntityTemplate* EntityTemplate =
            ValidatedBatchTemplates[VariantIndex];
        const int32 BeforeVariant = SpawnedEntities.Num();
        TSharedPtr<FMassEntityManager::FEntityCreationContext> CreationContext =
            SpawnerSubsystem->SpawnEntities(
                *EntityTemplate,
                VariantCount,
                SpawnedEntities);
        if (!CreationContext.IsValid() ||
            SpawnedEntities.Num() - BeforeVariant != VariantCount)
        {
            return false;
        }
        CreationContext.Reset();
        for (int32 VariantEntityIndex = 0;
             VariantEntityIndex < VariantCount;
             ++VariantEntityIndex)
        {
            CentralEntityVisualVariantIndices.Add(VariantIndex);
        }
    }
    if (SpawnedEntities.Num() - FirstEntityIndex != BatchCount)
    {
        return false;
    }

    for (int32 EntityIndex = FirstEntityIndex;
         EntityIndex < SpawnedEntities.Num();
         ++EntityIndex)
    {
        if (!CentralSpawnLanePlan.IsValidIndex(EntityIndex) ||
            !CentralSpawnDistancePlan.IsValidIndex(EntityIndex) ||
            !CentralSpawnPositionPlan.IsValidIndex(EntityIndex) ||
            !CentralSpawnDistrictPlan.IsValidIndex(EntityIndex) ||
            !InitializeCentralEntity(
                EntityIndex,
                CentralSpawnLanePlan[EntityIndex]))
        {
            return false;
        }
        ++RuntimeCentralDistricts[
            CentralSpawnDistrictPlan[EntityIndex]].AdmittedPopulation;
    }
    // Admission is a placement transaction, not part of pedestrian motion.
    // Keep every earlier batch stationary on its independently certified spawn
    // point so later batches are checked against the exact 100-slot plan. Once
    // the final batch commits, release the complete population together. This
    // prevents moving pedestrians from indefinitely occupying every reserve
    // candidate while admission is still in progress.
    const bool bFinalAdmissionBatch =
        SpawnedEntities.Num() == CentralAdmissionTargetCount;
    if (bFinalAdmissionBatch)
    {
        // Open the single movement gate before assigning the first paths.  This
        // is deliberately separate from the admitted count because telemetry
        // publishes that count every frame while admission is still running.
        bCentralAdmissionReleased = true;
        for (int32 EntityIndex = 0;
             EntityIndex < SpawnedEntities.Num();
             ++EntityIndex)
        {
            if (!PlanNewDestination(EntityIndex) ||
                !RequestNextPath(EntityIndex))
            {
                bCentralAdmissionReleased = false;
                return false;
            }
        }
        UE_LOG(
            LogTemp,
            Warning,
            TEXT("OPEN_MASS_CROWD_CENTRAL_ADMISSION_RELEASE population=%d policy=certify_all_then_move"),
            SpawnedEntities.Num());
    }

    // Mass creation, initialization and first routes all succeeded. Only now
    // publish replacement/minimum-clearance telemetry for this committed batch.
    for (const FCentralStagedAdmissionSlot& Staged : StagedSlots)
    {
        if (Staged.MinimumClearanceSquared < TNumericLimits<float>::Max())
        {
            CentralMinimumPlannedSpawnClearanceCm = FMath::Min(
                CentralMinimumPlannedSpawnClearanceCm,
                FMath::Sqrt(Staged.MinimumClearanceSquared));
        }
        if (Staged.ReserveIndex == INDEX_NONE)
        {
            continue;
        }
        ++CentralReserveReplacementCount;
        UE_LOG(
            LogTemp,
            Warning,
            TEXT("OPEN_MASS_CROWD_CENTRAL_RESERVE_REPLACEMENT entity=%d district=%s previous_lane=%d replacement_lane=%d reserve_index=%d reserve_count=%d live_probes=%d live_rejects=%d minimum_xy_clearance_cm=%.3f transactional=true mass_initialized=true"),
            Staged.PlanIndex,
            *RuntimeCentralDistricts[
                Staged.DistrictIndex].DistrictId.ToString(),
            Staged.PreviousLaneIndex,
            Staged.CachedSlot.LaneIndex,
            Staged.ReserveIndex,
            CentralReserveSpawnSlotsByPlanIndex[
                Staged.PlanIndex].Num(),
            CentralAdmissionLiveGroundProbeCount,
            CentralAdmissionLiveGroundRejectCount,
            Staged.MinimumClearanceSquared <
                    TNumericLimits<float>::Max()
                ? FMath::Sqrt(Staged.MinimumClearanceSquared)
                : -1.0f);
    }

    CentralMaximumCommittedAdmissionBatchSize = FMath::Max(
        CentralMaximumCommittedAdmissionBatchSize,
        BatchCount);
    ++CentralAdmissionBatchCount;
    CentralAdmittedEntityCount = SpawnedEntities.Num();
    CentralSimulatedEntityCount = SpawnedEntities.Num();
    ++CentralAdmissionClearanceScanCount;
    RecordCentralTelemetry(true);
    if (CentralSevereOverlapPairCount > 0)
    {
        CentralAdmissionClearanceViolationCount =
            CentralAdmissionClearanceViolationCount >
                    MAX_int32 - CentralSevereOverlapPairCount
                ? MAX_int32
                : CentralAdmissionClearanceViolationCount +
                    CentralSevereOverlapPairCount;
        UE_LOG(
            LogTemp,
            Error,
            TEXT("OPEN_MASS_CROWD_CENTRAL_ADMISSION_CLEARANCE_FAIL scan=%d violations=%d threshold_cm=%.2f"),
            CentralAdmissionClearanceScanCount,
            CentralSevereOverlapPairCount,
            CentralSevereOverlapDistanceCm);
        return false;
    }
    UE_LOG(
        LogTemp,
        Warning,
        TEXT("OPEN_MASS_CROWD_CENTRAL_ADMISSION_CLEARANCE_PASS scan=%d admitted=%d batch_size=%d maximum_committed_batch=%d threshold_cm=%.2f dynamic_slot_revalidation=true live_exact_xy=true bounded_batches=true live_probes=%d live_rejects=%d no_raw_support_observations=%d reserve_replacements=%d minimum_planned_clearance_cm=%.3f"),
        CentralAdmissionClearanceScanCount,
        SpawnedEntities.Num(),
        BatchCount,
        CentralMaximumCommittedAdmissionBatchSize,
        CentralSevereOverlapDistanceCm,
        CentralAdmissionLiveGroundProbeCount,
        CentralAdmissionLiveGroundRejectCount,
        CentralAdmissionNoRawSupportCount,
        CentralReserveReplacementCount,
        CentralMinimumPlannedSpawnClearanceCm);

    if (SpawnedEntities.Num() < CentralAdmissionTargetCount)
    {
        GetWorldTimerManager().SetTimer(
            CentralAdmissionTimer,
            this,
            &AOpenMassCrowdSpawner::ContinueCentralAdmission,
            FMath::Max(CentralAdmissionBatchInterval, 0.01f),
            false);
    }
    else
    {
        // The public 60-second gate starts only after the final transactional
        // admission batch has committed. Forced admission diagnostics execute
        // inside the actor/timer tick, before a newly created Mass entity has
        // received its first certified-transform processor pass; they remain
        // covered by CentralAdmissionClearanceViolationCount, but must not be
        // mixed into rendered steady-state collision or frame-time evidence.
        CentralPeakSevereOverlapPairCount = 0;
        CentralPeakSevereOverlapAgentCount = 0;
        CentralSevereOverlapPairObservationCount = 0;
        CentralInvalidPositionObservationCount = 0;
        CentralMinimumObservedEntityCenterDistanceCm = -1.0f;
        CentralTelemetryObservationCount = 0;
        CentralTelemetrySampleAccumulator = 0.0f;
        CentralTelemetryLastCertifiedPositions.SetNumZeroed(
            CentralAdmissionTargetCount);
        CentralTelemetryStationarySeconds.SetNumZeroed(
            CentralAdmissionTargetCount);
        CentralTelemetryPositionValid.SetNumZeroed(
            CentralAdmissionTargetCount);
        CentralFrameTimeSamples.Reset();
        CentralFrameTimeFirstSampleIndex = 0;
        CentralFrameTimeSampleCount = 0;
        CentralFrameTimeP50Ms = 0.0f;
        CentralFrameTimeP95Ms = 0.0f;
        CentralFrameTimeMaximumMs = 0.0f;
        CentralFrameTimeWindowSeconds = 0.0f;
        UE_LOG(
            LogTemp,
            Warning,
            TEXT("OPEN_MASS_CROWD_CENTRAL_STEADY_STATE_GATE_RESET admitted=%d admission_scans=%d admission_violations=%d rendered_mass_frames_begin_next_tick=true"),
            CentralAdmittedEntityCount,
            CentralAdmissionClearanceScanCount,
            CentralAdmissionClearanceViolationCount);
        UE_LOG(
            LogTemp,
            Warning,
            TEXT("OPEN_MASS_CROWD_CENTRAL_ADMISSION_COMPLETE admitted=%d target=%d batches=%d maximum_committed_batch=%d districts=%d district_admitted=%d,%d,%d,%d,%d,%d live_probes=%d live_rejects=%d no_raw_support_observations=%d reserve_replacements=%d reserve_cycle_defers=%d"),
            CentralAdmittedEntityCount,
            CentralAdmissionTargetCount,
            CentralAdmissionBatchCount,
            CentralMaximumCommittedAdmissionBatchSize,
            RuntimeCentralDistricts.Num(),
            RuntimeCentralDistricts[0].AdmittedPopulation,
            RuntimeCentralDistricts[1].AdmittedPopulation,
            RuntimeCentralDistricts[2].AdmittedPopulation,
            RuntimeCentralDistricts[3].AdmittedPopulation,
            RuntimeCentralDistricts[4].AdmittedPopulation,
            RuntimeCentralDistricts[5].AdmittedPopulation,
            CentralAdmissionLiveGroundProbeCount,
            CentralAdmissionLiveGroundRejectCount,
            CentralAdmissionNoRawSupportCount,
            CentralReserveReplacementCount,
            CentralReserveCycleDeferCount);
    }
    return true;
}

bool AOpenMassCrowdSpawner::InitializeCentralEntity(
    const int32 EntityIndex,
    const int32 RuntimeLaneIndex)
{
    UMassSpawnerSubsystem* SpawnerSubsystem =
        UWorld::GetSubsystem<UMassSpawnerSubsystem>(GetWorld());
    UZoneGraphSubsystem* ZoneGraphSubsystem =
        UWorld::GetSubsystem<UZoneGraphSubsystem>(GetWorld());
    UMassCrowdSubsystem* CrowdSubsystem =
        UWorld::GetSubsystem<UMassCrowdSubsystem>(GetWorld());
    if (!SpawnerSubsystem || !ZoneGraphSubsystem || !CrowdSubsystem ||
        !SpawnedEntities.IsValidIndex(EntityIndex) ||
        !RuntimeLaneHandles.IsValidIndex(RuntimeLaneIndex) ||
        !IsCentralRuntimeLaneAvailable(RuntimeLaneIndex))
    {
        return false;
    }

    FMassEntityManager& EntityManager = SpawnerSubsystem->GetEntityManagerChecked();
    const FMassEntityHandle Entity = SpawnedEntities[EntityIndex];
    const FZoneGraphLaneHandle LaneHandle = RuntimeLaneHandles[RuntimeLaneIndex];
    float LaneLength = 0.0f;
    if (!EntityManager.IsEntityValid(Entity) ||
        !ZoneGraphSubsystem->GetLaneLength(LaneHandle, LaneLength) ||
        LaneLength <= 1.0f)
    {
        return false;
    }

    if (!CentralSpawnDistancePlan.IsValidIndex(EntityIndex) ||
        !CentralSpawnPositionPlan.IsValidIndex(EntityIndex) ||
        !CentralSpawnLivePositionPlan.IsValidIndex(EntityIndex) ||
        !CentralSpawnLivePositionValid.IsValidIndex(EntityIndex) ||
        CentralSpawnLivePositionValid[EntityIndex] == 0)
    {
        return false;
    }
    const float DistanceAlongLane = CentralSpawnDistancePlan[EntityIndex];
    const FVector CertifiedSpawnPosition = CentralSpawnPositionPlan[EntityIndex];
    const FVector LiveSpawnPosition =
        CentralSpawnLivePositionPlan[EntityIndex];
    if (!FMath::IsFinite(DistanceAlongLane) ||
        DistanceAlongLane <= 0.0f ||
        DistanceAlongLane >= LaneLength ||
        CertifiedSpawnPosition.ContainsNaN() ||
        LiveSpawnPosition.ContainsNaN())
    {
        return false;
    }
    const float SpawnPhase = FMath::Frac(
        (static_cast<float>(EntityIndex) + 1.0f) * 0.61803398875f);
    FZoneGraphLaneLocation SpawnLocation;
    if (!ZoneGraphSubsystem->CalculateLocationAlongLane(
        LaneHandle,
        DistanceAlongLane,
        SpawnLocation))
    {
        return false;
    }
    // Admission staged this independent live result transactionally in the
    // same game-thread pass. Do not probe a second time after Mass creation and
    // do not overwrite the immutable certified cache point with live Z.
    if (FVector::DistSquared2D(
            LiveSpawnPosition,
            CertifiedSpawnPosition) > 0.01f ||
        FMath::Square(
            LiveSpawnPosition.Z - CertifiedSpawnPosition.Z) >
            FMath::Square(GroundTolerance))
    {
        UE_LOG(
            LogTemp,
            Error,
            TEXT("OPEN_MASS_CROWD_CENTRAL_ADMISSION_STAGED_POSITION_INVALID entity=%d lane=%d xy_distance_cm=%.3f z_distance_cm=%.3f"),
            EntityIndex,
            RuntimeLaneIndex,
            FVector::Dist2D(
                LiveSpawnPosition,
                CertifiedSpawnPosition),
            FMath::Abs(
                LiveSpawnPosition.Z - CertifiedSpawnPosition.Z));
        return false;
    }
    const FTransform InitialTransform(
        SpawnLocation.Tangent.ToOrientationQuat(),
        LiveSpawnPosition);
    EntityManager.GetFragmentDataChecked<FTransformFragment>(Entity).SetTransform(
        InitialTransform);
    FOpenMassCrowdCentralVisualOwnerFragment& VisualOwner =
        EntityManager.GetFragmentDataChecked<
            FOpenMassCrowdCentralVisualOwnerFragment>(Entity);
    VisualOwner.Spawner = this;
    VisualOwner.EntityIndex = EntityIndex;
    FLastValidGroundState& InitialGroundState = LastValidGroundStates[EntityIndex];
    InitialGroundState.Transform = InitialTransform;
    InitialGroundState.LaneHandle = LaneHandle;
    InitialGroundState.DistanceAlongLane = DistanceAlongLane;
    InitialGroundState.LaneLength = LaneLength;
    InitialGroundState.bValid = true;
    if (RuntimeCentralPreviousFrameStates.IsValidIndex(EntityIndex))
    {
        RuntimeCentralPreviousFrameStates[EntityIndex] = InitialGroundState;
    }
    EntityManager.GetFragmentDataChecked<FAgentRadiusFragment>(Entity).Radius =
        CentralPedestrianRadius;

    if (FOpenMassCrowdVATPlaybackFragment* Playback =
        EntityManager.GetFragmentDataPtr<FOpenMassCrowdVATPlaybackFragment>(Entity))
    {
        Playback->TimeOffset = SpawnPhase * VATTimeOffsetSpread;
        Playback->PlayRate =
            0.9f + 0.01f * static_cast<float>((EntityIndex * 7) % 21);
    }

    FMassMoveTargetFragment& MoveTarget =
        EntityManager.GetFragmentDataChecked<FMassMoveTargetFragment>(Entity);
    MoveTarget.Center = LiveSpawnPosition;
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
    return true;
}

void AOpenMassCrowdSpawner::ContinueCentralAdmission()
{
    if (!AdmitNextCentralBatch())
    {
        UE_LOG(LogTemp, Error, TEXT("OPEN_MASS_CROWD_CENTRAL_ADMISSION_FAILED"));
        DestroyRuntimePopulation();
        ScheduleCentralSpawnRetry(TEXT("batched_admission_failed"));
    }
}

void AOpenMassCrowdSpawner::QueueCentralConflictReplan(
    const int32 EntityIndex,
    const TSet<int32>& ForbiddenLaneIndices)
{
    if (NetworkMode != EOpenMassCrowdNetworkMode::CentralCertifiedCache ||
        EntityIndex == INDEX_NONE)
    {
        return;
    }
    // A local-conflict waiter can still be inside its current lane, before a
    // concrete outgoing transition has been selected.  In that case the
    // forbidden set is intentionally empty: replanning must choose a fresh
    // occupancy-aware destination/first hop instead of silently discarding the
    // deadlock escape request.
    TSet<int32>& PendingForbidden =
        PendingCentralConflictReplans.FindOrAdd(EntityIndex);
    for (const int32 ForbiddenLaneIndex : ForbiddenLaneIndices)
    {
        PendingForbidden.Add(ForbiddenLaneIndex);
    }
}

void AOpenMassCrowdSpawner::ProcessCentralConflictReplans()
{
    if (!bCentralAdmissionReleased)
    {
        return;
    }
    if (PendingCentralConflictReplans.IsEmpty())
    {
        return;
    }
    TArray<int32> EntityIndices;
    PendingCentralConflictReplans.GetKeys(EntityIndices);
    EntityIndices.Sort();
    TMap<int32, TSet<int32>> Replans =
        MoveTemp(PendingCentralConflictReplans);
    PendingCentralConflictReplans.Reset();

    UMassSpawnerSubsystem* SpawnerSubsystem =
        UWorld::GetSubsystem<UMassSpawnerSubsystem>(GetWorld());
    UZoneGraphSubsystem* ZoneGraphSubsystem =
        UWorld::GetSubsystem<UZoneGraphSubsystem>(GetWorld());
    if (!SpawnerSubsystem || !ZoneGraphSubsystem)
    {
        return;
    }
    FMassEntityManager& EntityManager =
        SpawnerSubsystem->GetEntityManagerChecked();
    for (const int32 EntityIndex : EntityIndices)
    {
        TSet<int32>* ForbiddenLaneIndices = Replans.Find(EntityIndex);
        if (!ForbiddenLaneIndices ||
            !SpawnedEntities.IsValidIndex(EntityIndex) ||
            !EntityManager.IsEntityValid(SpawnedEntities[EntityIndex]))
        {
            continue;
        }
        const FMassZoneGraphLaneLocationFragment* CurrentLane =
            EntityManager.GetFragmentDataPtr<
                FMassZoneGraphLaneLocationFragment>(
                SpawnedEntities[EntityIndex]);
        if (!CurrentLane)
        {
            continue;
        }
        ForbiddenLaneIndices->Remove(CurrentLane->LaneHandle.Index);

        const FEntityRouteState PreviousRouteState =
            EntityRouteStates[EntityIndex];
        const int32 PreviousRouteAssignmentCount = RouteAssignmentCount;
        const bool bPlanned = PlanNewCentralDestination(
            EntityIndex,
            ForbiddenLaneIndices);
        const bool bPathHandled = bPlanned && RequestNextPath(EntityIndex);
        const bool bPathActivated = bPathHandled &&
            !EntityRouteStates[EntityIndex].bWaitingForAvailableCell;
        if (bPathActivated)
        {
            ++CentralConflictWaitReplanCount;
            ++RouteReplanCount;
            // Replanning is not proof that the contested resource changed or
            // was cleared. Preserve FIFO age here; the reservation scheduler
            // resets it only after the entity actually leaves or changes the
            // corridor/local resource.
            UE_LOG(
                LogTemp,
                Warning,
                TEXT("OPEN_MASS_CROWD_CENTRAL_CONFLICT_REPLAN entity=%d current_lane=%d forbidden_lanes=%d wait_limit_s=%.2f status=success"),
                EntityIndex,
                CurrentLane->LaneHandle.Index,
                ForbiddenLaneIndices->Num(),
                CentralConflictWaitReplanSeconds);
        }
        else
        {
            // Planning commits the candidate route before path activation.
            // A failed activation must not leave a half-committed route or
            // inflate the assignment counter.  Keep fail-closed waiting state
            // intact because it is a deliberate safety result, not success.
            if (bPlanned &&
                !EntityRouteStates[EntityIndex].bWaitingForAvailableCell)
            {
                EntityRouteStates[EntityIndex] = PreviousRouteState;
                RouteAssignmentCount = PreviousRouteAssignmentCount;
            }
            UE_LOG(
                LogTemp,
                Warning,
                TEXT("OPEN_MASS_CROWD_CENTRAL_CONFLICT_REPLAN entity=%d current_lane=%d forbidden_lanes=%d wait_limit_s=%.2f status=retry"),
                EntityIndex,
                CurrentLane->LaneHandle.Index,
                ForbiddenLaneIndices->Num(),
                CentralConflictWaitReplanSeconds);
        }
    }
}

bool AOpenMassCrowdSpawner::PlanNewDestination(const int32 EntityIndex)
{
    if (NetworkMode == EOpenMassCrowdNetworkMode::CentralCertifiedCache)
    {
        return PlanNewCentralDestination(EntityIndex);
    }

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

bool AOpenMassCrowdSpawner::PlanNewCentralDestination(
    const int32 EntityIndex,
    const TSet<int32>* ForbiddenLaneIndices)
{
    UMassSpawnerSubsystem* SpawnerSubsystem =
        UWorld::GetSubsystem<UMassSpawnerSubsystem>(GetWorld());
    UZoneGraphSubsystem* ZoneGraphSubsystem =
        UWorld::GetSubsystem<UZoneGraphSubsystem>(GetWorld());
    if (!SpawnerSubsystem || !ZoneGraphSubsystem || !IsValid(RuntimeZoneGraphData) ||
        !SpawnedEntities.IsValidIndex(EntityIndex) ||
        !EntityRouteStates.IsValidIndex(EntityIndex) ||
        RuntimeLaneHandles.Num() < 2 ||
        RuntimeCentralLaneCellIds.Num() != RuntimeLaneHandles.Num() ||
        RuntimeCentralLaneComponentIds.Num() != RuntimeLaneHandles.Num() ||
        RuntimeCentralReverseLaneIndices.Num() != RuntimeLaneHandles.Num() ||
        RuntimeCentralCollisionConflictLaneIndices.Num() !=
            RuntimeLaneHandles.Num() ||
        RuntimeCentralCrossingFlags.Num() != RuntimeLaneHandles.Num())
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
    const int32 CurrentLaneIndex = CurrentLane.LaneHandle.Index;
    if (!IsCentralRuntimeLaneAvailable(CurrentLaneIndex))
    {
        return false;
    }

    FEntityRouteState& RouteState = EntityRouteStates[EntityIndex];
    if (RouteState.LastObservedLaneIndex != CurrentLaneIndex)
    {
        RouteState.LastObservedLaneIndex = CurrentLaneIndex;
        RouteState.RecentLaneIndices.Add(CurrentLaneIndex);
        if (RouteState.RecentLaneIndices.Num() > CentralLaneHistoryLimit)
        {
            RouteState.RecentLaneIndices.RemoveAt(
                0,
                RouteState.RecentLaneIndices.Num() - CentralLaneHistoryLimit);
        }
    }

    FZoneGraphLaneLocation StartLocation;
    if (!ZoneGraphSubsystem->CalculateLocationAlongLane(
        CurrentLane.LaneHandle,
        CurrentLane.DistanceAlongLane,
        StartLocation))
    {
        return false;
    }

    if constexpr (bUseCentralCertifiedEdgeCirculation)
    {
        const int32 ReverseLaneIndex =
            RuntimeCentralReverseLaneIndices.IsValidIndex(CurrentLaneIndex)
            ? RuntimeCentralReverseLaneIndices[CurrentLaneIndex]
            : INDEX_NONE;
        if (!RuntimeLaneHandles.IsValidIndex(ReverseLaneIndex) ||
            ReverseLaneIndex == CurrentLaneIndex ||
            !IsCentralRuntimeLaneAvailable(ReverseLaneIndex) ||
            (ForbiddenLaneIndices &&
             ForbiddenLaneIndices->Contains(ReverseLaneIndex)))
        {
            return false;
        }

        float ReverseLaneLength = 0.0f;
        if (!ZoneGraphSubsystem->GetLaneLength(
                RuntimeLaneHandles[ReverseLaneIndex],
                ReverseLaneLength) ||
            ReverseLaneLength <= 1.0f)
        {
            return false;
        }

        // Pedestrians sharing an edge circulation must also share its period.
        // Using the lane pair for both speed and turn distance preserves their
        // admission spacing instead of letting one eventually catch another.
        const int32 PairKey = FMath::Min(CurrentLaneIndex, ReverseLaneIndex);
        const float DestinationFraction =
            0.65f + 0.2f * static_cast<float>((PairKey * 37) % 101) / 100.0f;
        RouteState.Reset();
        const FZoneGraphDataHandle DataHandle =
            RuntimeZoneGraphData->GetStorage().DataHandle;
        RouteState.LanePath.Emplace(CurrentLaneIndex, DataHandle);
        RouteState.LanePath.Emplace(ReverseLaneIndex, DataHandle);
        RouteState.CurrentPathIndex = 0;
        RouteState.DestinationDistance =
            ReverseLaneLength * DestinationFraction;
        RouteState.PlannedAvailabilityRevision = CentralAvailabilityRevision;
        RouteState.RecentDestinationLaneIndices.Add(ReverseLaneIndex);
        if (RouteState.RecentDestinationLaneIndices.Num() >
            CentralDestinationHistoryLimit)
        {
            RouteState.RecentDestinationLaneIndices.RemoveAt(
                0,
                RouteState.RecentDestinationLaneIndices.Num() -
                    CentralDestinationHistoryLimit);
        }
        ++RouteAssignmentCount;

        UE_LOG(
            LogTemp,
            Log,
            TEXT("OPEN_MASS_CROWD_CENTRAL_ROUTE entity=%d trip=%d start_lane=%d destination_lane=%d path_lanes=2 policy=certified_edge_circulation destination_cm=%.1f"),
            EntityIndex,
            RouteState.CompletedTrips + 1,
            CurrentLaneIndex,
            ReverseLaneIndex,
            RouteState.DestinationDistance);
        return true;
    }

    TArray<int32> LaneOccupancies;
    LaneOccupancies.SetNumZeroed(RuntimeLaneHandles.Num());
    for (const FMassEntityHandle OtherEntity : SpawnedEntities)
    {
        if (!EntityManager.IsEntityValid(OtherEntity))
        {
            continue;
        }
        const FMassZoneGraphLaneLocationFragment* OtherLane =
            EntityManager.GetFragmentDataPtr<FMassZoneGraphLaneLocationFragment>(OtherEntity);
        if (OtherLane && LaneOccupancies.IsValidIndex(OtherLane->LaneHandle.Index))
        {
            ++LaneOccupancies[OtherLane->LaneHandle.Index];
        }
    }
    TArray<int32> CorridorOccupancies;
    CorridorOccupancies.SetNumZeroed(LaneOccupancies.Num());
    for (int32 LaneIndex = 0; LaneIndex < LaneOccupancies.Num(); ++LaneIndex)
    {
        int32 ConflictOccupancy = LaneOccupancies[LaneIndex];
        for (const int32 ConflictLaneIndex :
             RuntimeCentralCollisionConflictLaneIndices[LaneIndex])
        {
            if (LaneOccupancies.IsValidIndex(ConflictLaneIndex))
            {
                ConflictOccupancy = ConflictOccupancy >
                        MAX_int32 - LaneOccupancies[ConflictLaneIndex]
                    ? MAX_int32
                    : ConflictOccupancy +
                        LaneOccupancies[ConflictLaneIndex];
            }
        }
        CorridorOccupancies[LaneIndex] = ConflictOccupancy;
    }

    TArray<uint8> AvailabilityFlags;
    AvailabilityFlags.Reserve(RuntimeCentralLaneCellIds.Num());
    for (int32 LaneIndex = 0;
         LaneIndex < RuntimeCentralLaneCellIds.Num();
         ++LaneIndex)
    {
        const bool bForbidden = ForbiddenLaneIndices &&
            LaneIndex != CurrentLaneIndex &&
            ForbiddenLaneIndices->Contains(LaneIndex);
        AvailabilityFlags.Add(
            RuntimeUnavailableCentralCellIds.Contains(
                RuntimeCentralLaneCellIds[LaneIndex]) ||
                bForbidden
            ? 0
            : 1);
    }

    const FZoneGraphStorage& Storage = RuntimeZoneGraphData->GetStorage();
    TArray<FZoneGraphAStarWrapper::FNodeRef> SelectedPath;
    float SelectedDestinationDistance = 0.0f;
    int32 SelectedDestinationLaneIndex = INDEX_NONE;
    double SelectedScore = TNumericLimits<double>::Max();
    float SelectedOutboundDistanceCm = 0.0f;
    float LongestReachableOutboundDistanceCm = 0.0f;
    bool bSelectedPreferredDistance = false;
    int32 SelectedCrossingLaneCount = 0;
    bool bUsedFullDestinationFallback = false;
    const float PreferredOutboundTargetCm =
        CentralPreferredOutboundMinimumCm + static_cast<float>(
            (static_cast<uint64>(EntityIndex) * 7919ULL) %
            static_cast<uint64>(
                CentralPreferredOutboundMaximumCm -
                CentralPreferredOutboundMinimumCm + 1.0f));
    const int32 FirstDestinationLaneIndex =
        RouteRandomStream.RandRange(0, RuntimeLaneHandles.Num() - 1);
    const FName CurrentComponentId =
        RuntimeCentralLaneComponentIds[CurrentLaneIndex];
    int32 SuccessfulCandidates = 0;
    TArray<uint8> AttemptedDestinationLanes;
    AttemptedDestinationLanes.SetNumZeroed(RuntimeLaneHandles.Num());
    const auto TryDestinationLane =
        [&](const int32 DestinationLaneIndex, const bool bRequireDifferentCell)
    {
        if (!AttemptedDestinationLanes.IsValidIndex(DestinationLaneIndex) ||
            AttemptedDestinationLanes[DestinationLaneIndex] != 0)
        {
            return false;
        }
        AttemptedDestinationLanes[DestinationLaneIndex] = 1;
        if (DestinationLaneIndex == CurrentLaneIndex ||
            (ForbiddenLaneIndices &&
             ForbiddenLaneIndices->Contains(DestinationLaneIndex)) ||
            !IsCentralRuntimeLaneAvailable(DestinationLaneIndex) ||
            RuntimeCentralLaneComponentIds[DestinationLaneIndex] !=
                CurrentComponentId ||
            (bRequireDifferentCell &&
             RuntimeCentralLaneCellIds[DestinationLaneIndex] ==
                RuntimeCentralLaneCellIds[CurrentLaneIndex]))
        {
            return false;
        }

        float DestinationLaneLength = 0.0f;
        if (!ZoneGraphSubsystem->GetLaneLength(
                RuntimeLaneHandles[DestinationLaneIndex],
                DestinationLaneLength) ||
            DestinationLaneLength <= 1.0f)
        {
            return false;
        }
        // Finish at the certified lane endpoint (with a one-centimetre
        // numerical inset). The return leg can then enter the certified
        // reverse lane at the real graph node and retrace the outbound route
        // without a teleport or a mid-lane U-turn.
        const float DestinationDistance = FMath::Max(
            CentralRouteEndpointInsetCm,
            DestinationLaneLength - CentralRouteEndpointInsetCm);
        FZoneGraphLaneLocation EndLocation;
        if (!ZoneGraphSubsystem->CalculateLocationAlongLane(
                RuntimeLaneHandles[DestinationLaneIndex],
                DestinationDistance,
                EndLocation))
        {
            return false;
        }

        FZoneGraphAStarWrapper Graph(Storage);
        FZoneGraphAStar Pathfinder(Graph);
        const FOpenMassCrowdCentralPathFilter PathFilter(
            Storage,
            StartLocation,
            EndLocation,
            CorridorOccupancies,
            RuntimeCentralReverseLaneIndices,
            RuntimeCentralCrossingFlags,
            AvailabilityFlags,
            RouteState.RecentLaneIndices);
        TArray<FZoneGraphAStarWrapper::FNodeRef> CandidatePath;
        const EGraphAStarResult Result = Pathfinder.FindPath(
            FZoneGraphAStarNode(CurrentLaneIndex, StartLocation.Position),
            FZoneGraphAStarNode(DestinationLaneIndex, EndLocation.Position),
            PathFilter,
            CandidatePath);
        if (Result != EGraphAStarResult::SearchSuccess ||
            CandidatePath.Num() < 2 ||
            CandidatePath[0] != CurrentLaneIndex)
        {
            return false;
        }

        float CandidateOutboundDistanceCm = 0.0f;
        double CongestionCost = 0.0;
        int32 CrossingLaneCount = 0;
        for (int32 PathIndex = 0;
             PathIndex < CandidatePath.Num();
             ++PathIndex)
        {
            const int32 LaneIndex = CandidatePath[PathIndex];
            float LaneLength = 0.0f;
            if (!RuntimeLaneHandles.IsValidIndex(LaneIndex) ||
                !ZoneGraphSubsystem->GetLaneLength(
                    RuntimeLaneHandles[LaneIndex],
                    LaneLength) ||
                LaneLength <= 0.0f)
            {
                return false;
            }
            if (PathIndex == 0)
            {
                CandidateOutboundDistanceCm += FMath::Max(
                    0.0f,
                    LaneLength - CurrentLane.DistanceAlongLane);
            }
            else if (PathIndex == CandidatePath.Num() - 1)
            {
                CandidateOutboundDistanceCm += DestinationDistance;
            }
            else
            {
                CandidateOutboundDistanceCm += LaneLength;
            }
            const float Capacity = FMath::Max(1.0f, LaneLength / 140.0f);
            const float Density = CorridorOccupancies.IsValidIndex(LaneIndex)
                ? static_cast<float>(CorridorOccupancies[LaneIndex]) / Capacity
                : 0.0f;
            CongestionCost += LaneLength *
                (1.0 + FMath::Clamp(Density, 0.0f, 4.0f) * 0.65f);
            CrossingLaneCount += RuntimeCentralCrossingFlags[LaneIndex] != 0 ? 1 : 0;
        }
        LongestReachableOutboundDistanceCm = FMath::Max(
            LongestReachableOutboundDistanceCm,
            CandidateOutboundDistanceCm);
        const bool bCandidatePreferredDistance =
            CandidateOutboundDistanceCm >= CentralPreferredOutboundMinimumCm;
        double CandidateScore = FMath::Abs(
            CandidateOutboundDistanceCm - PreferredOutboundTargetCm);
        if (CandidateOutboundDistanceCm > CentralPreferredOutboundMaximumCm)
        {
            CandidateScore +=
                (CandidateOutboundDistanceCm -
                 CentralPreferredOutboundMaximumCm) * 2.0;
        }
        CandidateScore += CongestionCost * 0.05;
        CandidateScore += static_cast<double>(CrossingLaneCount) * 200.0;
        CandidateScore += RouteRandomStream.FRandRange(0.0f, 25.0f);
        const int32 RecentDestinationIndex =
            RouteState.RecentDestinationLaneIndices.FindLast(DestinationLaneIndex);
        if (RecentDestinationIndex != INDEX_NONE)
        {
            CandidateScore += 50000.0;
        }

        ++SuccessfulCandidates;
        const bool bShouldSelect =
            (bCandidatePreferredDistance && !bSelectedPreferredDistance) ||
            (bCandidatePreferredDistance == bSelectedPreferredDistance &&
             (bCandidatePreferredDistance
                  ? CandidateScore < SelectedScore
                  : CandidateOutboundDistanceCm >
                        SelectedOutboundDistanceCm + KINDA_SMALL_NUMBER));
        if (bShouldSelect)
        {
            SelectedScore = CandidateScore;
            SelectedPath = MoveTemp(CandidatePath);
            SelectedDestinationDistance = DestinationDistance;
            SelectedDestinationLaneIndex = DestinationLaneIndex;
            SelectedOutboundDistanceCm = CandidateOutboundDistanceCm;
            bSelectedPreferredDistance = bCandidatePreferredDistance;
            SelectedCrossingLaneCount = CrossingLaneCount;
        }
        return false;
    };

    // The bounded pass keeps normal route planning predictable. If that
    // contiguous window happens to contain only the current cell (or no
    // reachable cross-cell destination), perform one deterministic full scan
    // before failing closed. This cannot miss a valid destination merely
    // because stable lane IDs clustered the first 128 lanes by cell.
    const int32 BoundedDestinationAttemptCount = RuntimeLaneHandles.Num();
    for (int32 Attempt = 0;
         Attempt < BoundedDestinationAttemptCount;
         ++Attempt)
    {
        const int32 DestinationLaneIndex =
            (FirstDestinationLaneIndex + Attempt) % RuntimeLaneHandles.Num();
        if (TryDestinationLane(DestinationLaneIndex, true))
        {
            break;
        }
    }
    bUsedFullDestinationFallback = true;

    // A fully certified component may be contained in one cell. If it has no
    // cross-cell destination, retry within the same component only; never ask
    // A* to search across a certified gap.
    // Also evaluate same-cell destinations. Ground components are the safety
    // boundary; a useful 60 m path may stay within one Central grid cell.
    // Preserve any better cross-cell candidate already selected above.
    {
        AttemptedDestinationLanes.Init(0, RuntimeLaneHandles.Num());
        for (int32 Attempt = 0; Attempt < RuntimeLaneHandles.Num(); ++Attempt)
        {
            const int32 DestinationLaneIndex =
                (FirstDestinationLaneIndex + Attempt) % RuntimeLaneHandles.Num();
            if (TryDestinationLane(DestinationLaneIndex, false))
            {
                break;
            }
        }
    }

    if (SelectedPath.Num() < 2 || SelectedDestinationLaneIndex == INDEX_NONE)
    {
        UE_LOG(
            LogTemp,
            Warning,
            TEXT("OPEN_MASS_CROWD_CENTRAL_ROUTE_WAIT entity=%d current_lane=%d unavailable_cells=%d"),
            EntityIndex,
            CurrentLaneIndex,
            RuntimeUnavailableCentralCellIds.Num());
        return false;
    }

    RouteState.Reset();
    RouteState.LanePath.Reserve(SelectedPath.Num());
    for (const int32 LaneIndex : SelectedPath)
    {
        RouteState.LanePath.Emplace(LaneIndex, Storage.DataHandle);
    }
    RouteState.CurrentPathIndex = 0;
    RouteState.DestinationDistance = SelectedDestinationDistance;
    RouteState.PlannedOutboundDistanceCm = SelectedOutboundDistanceCm;
    RouteState.PlannedRoundTripDistanceCm =
        SelectedOutboundDistanceCm * 2.0f;
    RouteState.bSmallComponentFallback = !bSelectedPreferredDistance;
    RouteState.bOnReturnLeg = false;
    RouteState.PlannedAvailabilityRevision = CentralAvailabilityRevision;

    // Keep the destination lane as a zero/one-centimetre anchor so the next
    // short-path request transitions from the actual current lane into its
    // certified reverse. The remaining lanes are the exact reverse mapping of
    // the outbound path in reverse order.
    RouteState.ReturnLanePath.Reserve(SelectedPath.Num() + 1);
    RouteState.ReturnLanePath.Emplace(
        SelectedDestinationLaneIndex,
        Storage.DataHandle);
    for (int32 PathIndex = SelectedPath.Num() - 1;
         PathIndex >= 0;
         --PathIndex)
    {
        const int32 OutboundLaneIndex = SelectedPath[PathIndex];
        const int32 ReverseLaneIndex =
            RuntimeCentralReverseLaneIndices.IsValidIndex(OutboundLaneIndex)
            ? RuntimeCentralReverseLaneIndices[OutboundLaneIndex]
            : INDEX_NONE;
        if (!RuntimeLaneHandles.IsValidIndex(ReverseLaneIndex) ||
            !IsCentralRuntimeLaneAvailable(ReverseLaneIndex) ||
            (ForbiddenLaneIndices &&
             ForbiddenLaneIndices->Contains(ReverseLaneIndex)))
        {
            RouteState.Reset();
            return false;
        }
        RouteState.ReturnLanePath.Emplace(
            ReverseLaneIndex,
            Storage.DataHandle);
    }
    float StartLaneLength = 0.0f;
    if (!ZoneGraphSubsystem->GetLaneLength(
            CurrentLane.LaneHandle,
            StartLaneLength) ||
        StartLaneLength <= CentralRouteEndpointInsetCm * 2.0f)
    {
        RouteState.Reset();
        return false;
    }
    RouteState.ReturnDestinationDistance = FMath::Clamp(
        StartLaneLength - CurrentLane.DistanceAlongLane,
        CentralRouteEndpointInsetCm,
        StartLaneLength - CentralRouteEndpointInsetCm);
    RouteState.RecentDestinationLaneIndices.Add(SelectedDestinationLaneIndex);
    if (RouteState.RecentDestinationLaneIndices.Num() > CentralDestinationHistoryLimit)
    {
        RouteState.RecentDestinationLaneIndices.RemoveAt(
            0,
            RouteState.RecentDestinationLaneIndices.Num() -
                CentralDestinationHistoryLimit);
    }
    ++RouteAssignmentCount;

    UE_LOG(
        LogTemp,
        Log,
        TEXT("OPEN_MASS_CROWD_CENTRAL_ROUTE entity=%d trip=%d start_lane=%d destination_lane=%d path_lanes=%d return_lanes=%d outbound_m=%.2f round_trip_m=%.2f preferred_target_m=%.2f small_component_fallback=%s longest_reachable_m=%.2f reverse_core_exact=true crossings=%d conflict_occupancy_destination=%d score=%.1f availability_revision=%d forbidden_lanes=%d full_scan=%s"),
        EntityIndex,
        RouteState.CompletedTrips + 1,
        CurrentLaneIndex,
        SelectedDestinationLaneIndex,
        RouteState.LanePath.Num(),
        RouteState.ReturnLanePath.Num(),
        RouteState.PlannedOutboundDistanceCm / 100.0f,
        RouteState.PlannedRoundTripDistanceCm / 100.0f,
        PreferredOutboundTargetCm / 100.0f,
        RouteState.bSmallComponentFallback ? TEXT("true") : TEXT("false"),
        LongestReachableOutboundDistanceCm / 100.0f,
        SelectedCrossingLaneCount,
        CorridorOccupancies[SelectedDestinationLaneIndex],
        SelectedScore,
        CentralAvailabilityRevision,
        ForbiddenLaneIndices ? ForbiddenLaneIndices->Num() : 0,
        bUsedFullDestinationFallback ? TEXT("true") : TEXT("false"));
    return true;
}

bool AOpenMassCrowdSpawner::ActivateCentralReturnRoute(
    const int32 EntityIndex)
{
    UMassSpawnerSubsystem* SpawnerSubsystem =
        UWorld::GetSubsystem<UMassSpawnerSubsystem>(GetWorld());
    if (!SpawnerSubsystem ||
        !SpawnedEntities.IsValidIndex(EntityIndex) ||
        !EntityRouteStates.IsValidIndex(EntityIndex))
    {
        return false;
    }

    FMassEntityManager& EntityManager =
        SpawnerSubsystem->GetEntityManagerChecked();
    const FMassEntityHandle Entity = SpawnedEntities[EntityIndex];
    if (!EntityManager.IsEntityValid(Entity))
    {
        return false;
    }
    const FMassZoneGraphLaneLocationFragment& CurrentLane =
        EntityManager.GetFragmentDataChecked<
            FMassZoneGraphLaneLocationFragment>(Entity);
    FEntityRouteState& RouteState = EntityRouteStates[EntityIndex];
    if (RouteState.bOnReturnLeg ||
        RouteState.ReturnLanePath.Num() < 3 ||
        RouteState.ReturnLanePath[0] != CurrentLane.LaneHandle ||
        RouteState.ReturnDestinationDistance <= 0.0f)
    {
        return false;
    }
    for (const FZoneGraphLaneHandle LaneHandle : RouteState.ReturnLanePath)
    {
        if (!RuntimeLaneHandles.IsValidIndex(LaneHandle.Index) ||
            !IsCentralRuntimeLaneAvailable(LaneHandle.Index))
        {
            return false;
        }
    }

    RouteState.LanePath = RouteState.ReturnLanePath;
    RouteState.CurrentPathIndex = 0;
    RouteState.DestinationDistance = RouteState.ReturnDestinationDistance;
    RouteState.bOnReturnLeg = true;
    RouteState.bWaitingForAvailableCell = false;
    RouteState.PlannedAvailabilityRevision = CentralAvailabilityRevision;
    RouteState.RecentDestinationLaneIndices.Add(
        RouteState.LanePath.Last().Index);
    if (RouteState.RecentDestinationLaneIndices.Num() >
        CentralDestinationHistoryLimit)
    {
        RouteState.RecentDestinationLaneIndices.RemoveAt(
            0,
            RouteState.RecentDestinationLaneIndices.Num() -
                CentralDestinationHistoryLimit);
    }
    ++RouteAssignmentCount;
    UE_LOG(
        LogTemp,
        Log,
        TEXT("OPEN_MASS_CROWD_CENTRAL_RETURN entity=%d trip=%d start_lane=%d destination_lane=%d path_lanes=%d outbound_m=%.2f reverse_core_exact=true"),
        EntityIndex,
        RouteState.CompletedTrips + 1,
        CurrentLane.LaneHandle.Index,
        RouteState.LanePath.Last().Index,
        RouteState.LanePath.Num(),
        RouteState.PlannedOutboundDistanceCm / 100.0f);
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
    if (NetworkMode == EOpenMassCrowdNetworkMode::CentralCertifiedCache)
    {
        const int32 ActualLaneIndex = LaneLocation.LaneHandle.Index;
        if (RouteState.LastObservedLaneIndex != ActualLaneIndex)
        {
            RouteState.LastObservedLaneIndex = ActualLaneIndex;
            RouteState.RecentLaneIndices.Add(ActualLaneIndex);
            if (RouteState.RecentLaneIndices.Num() > CentralLaneHistoryLimit)
            {
                RouteState.RecentLaneIndices.RemoveAt(
                    0,
                    RouteState.RecentLaneIndices.Num() - CentralLaneHistoryLimit);
            }
        }
        if (!IsCentralRuntimeLaneAvailable(ActualLaneIndex))
        {
            return HoldCentralEntityAtCertifiedPosition(
                EntityIndex,
                TEXT("current_cell_unavailable"));
        }

        bool bRouteTouchesUnavailableCell = false;
        for (int32 PathIndex = FMath::Max(RouteState.CurrentPathIndex, 0);
             PathIndex < RouteState.LanePath.Num();
             ++PathIndex)
        {
            if (!IsCentralRuntimeLaneAvailable(
                    RouteState.LanePath[PathIndex].Index))
            {
                bRouteTouchesUnavailableCell = true;
                break;
            }
        }
        if (RouteState.bWaitingForAvailableCell || bRouteTouchesUnavailableCell)
        {
            ++RouteReplanCount;
            if (!PlanNewDestination(EntityIndex))
            {
                return HoldCentralEntityAtCertifiedPosition(
                    EntityIndex,
                    TEXT("no_certified_detour"));
            }
        }
    }

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
            return NetworkMode == EOpenMassCrowdNetworkMode::CentralCertifiedCache
                ? HoldCentralEntityAtCertifiedPosition(
                    EntityIndex,
                    TEXT("replan_failed"))
                : false;
        }
        CurrentRouteIndex = 0;
    }

    RouteState.CurrentPathIndex = CurrentRouteIndex;
    if (NetworkMode == EOpenMassCrowdNetworkMode::CentralCertifiedCache &&
        RouteState.LanePath.IsValidIndex(CurrentRouteIndex + 1) &&
        LaneLocation.DistanceAlongLane >=
            LaneLocation.LaneLength - CentralRouteEndpointInsetCm)
    {
        const FZoneGraphLaneHandle PreviousLaneHandle =
            LaneLocation.LaneHandle;
        const FZoneGraphLaneHandle NextLaneHandle =
            RouteState.LanePath[CurrentRouteIndex + 1];
        const int32 NextLaneIndex = NextLaneHandle.Index;
        bool bNextLaneEntranceOccupied = false;
        const TArray<int32>* SameDirectionNextLanes =
            RuntimeCentralSameDirectionPhysicalLaneIndices.IsValidIndex(
                NextLaneIndex)
            ? &RuntimeCentralSameDirectionPhysicalLaneIndices[NextLaneIndex]
            : nullptr;
        const float RequiredEntryProgressCm =
            CentralMinimumCenterClearanceCm +
            CentralShortPathCacheToleranceCm;
        for (int32 OtherEntityIndex = 0;
             OtherEntityIndex < SpawnedEntities.Num();
             ++OtherEntityIndex)
        {
            if (OtherEntityIndex == EntityIndex)
            {
                continue;
            }
            const FMassEntityHandle OtherEntity =
                SpawnedEntities[OtherEntityIndex];
            if (!EntityManager.IsEntityValid(OtherEntity))
            {
                continue;
            }
            const FMassZoneGraphLaneLocationFragment* OtherLane =
                EntityManager.GetFragmentDataPtr<
                    FMassZoneGraphLaneLocationFragment>(OtherEntity);
            if (!OtherLane ||
                OtherLane->DistanceAlongLane >= RequiredEntryProgressCm ||
                (bInvestorDeliveryDemoEnabled &&
                 GetInvestorPresentationBandIndex(OtherEntityIndex) !=
                     GetInvestorPresentationBandIndex(EntityIndex)))
            {
                continue;
            }
            const int32 OtherLaneIndex = OtherLane->LaneHandle.Index;
            if (OtherLaneIndex == NextLaneIndex ||
                (SameDirectionNextLanes &&
                 SameDirectionNextLanes->Contains(OtherLaneIndex)))
            {
                bNextLaneEntranceOccupied = true;
                break;
            }
        }

        float NextLaneLength = 0.0f;
        FZoneGraphLaneLocation PreviousEndpoint;
        FZoneGraphLaneLocation NextStart;
        UMassCrowdSubsystem* CrowdSubsystem =
            UWorld::GetSubsystem<UMassCrowdSubsystem>(GetWorld());
        if (!bNextLaneEntranceOccupied && CrowdSubsystem &&
            IsCentralRuntimeLaneAvailable(NextLaneIndex) &&
            ZoneGraphSubsystem->GetLaneLength(
                NextLaneHandle,
                NextLaneLength) &&
            NextLaneLength > CentralRouteEndpointInsetCm * 2.0f &&
            ZoneGraphSubsystem->CalculateLocationAlongLane(
                PreviousLaneHandle,
                LaneLocation.LaneLength,
                PreviousEndpoint) &&
            ZoneGraphSubsystem->CalculateLocationAlongLane(
                NextLaneHandle,
                0.0f,
                NextStart))
        {
            FMassCrowdLaneTrackingFragment& LaneTracking =
                EntityManager.GetFragmentDataChecked<
                    FMassCrowdLaneTrackingFragment>(Entity);
            CrowdSubsystem->OnEntityLaneChanged(
                Entity,
                PreviousLaneHandle,
                NextLaneHandle);
            LaneTracking.TrackedLaneHandle = NextLaneHandle;
            LaneLocation.LaneHandle = NextLaneHandle;
            LaneLocation.DistanceAlongLane = 0.0f;
            LaneLocation.LaneLength = NextLaneLength;
            ++CurrentRouteIndex;
            RouteState.CurrentPathIndex = CurrentRouteIndex;
            UE_LOG(
                LogTemp,
                Verbose,
                TEXT("OPEN_MASS_CROWD_CENTRAL_ENDPOINT_HANDOFF entity=%d previous_lane=%d next_lane=%d node_gap_cm=%.3f policy=connected_certified_node_no_teleport"),
                EntityIndex,
                PreviousLaneHandle.Index,
                NextLaneIndex,
                FVector::Distance(
                    PreviousEndpoint.Position,
                    NextStart.Position));
        }
    }
    if (CurrentRouteIndex == RouteState.LanePath.Num() - 1 &&
        LaneLocation.DistanceAlongLane >= RouteState.DestinationDistance - 5.0f)
    {
        // Preserve the trip number while planning so its route log is correct,
        // but roll it back if a new destination cannot be created. This avoids
        // counting the same finished path again on a later retry.
        ++RouteState.CompletedTrips;
        const bool bCompletedReturnLeg =
            NetworkMode == EOpenMassCrowdNetworkMode::CentralCertifiedCache &&
            RouteState.bOnReturnLeg;
        bool bPlannedNextLeg = false;
        if (NetworkMode == EOpenMassCrowdNetworkMode::CentralCertifiedCache &&
            !RouteState.bOnReturnLeg &&
            !RouteState.ReturnLanePath.IsEmpty())
        {
            bPlannedNextLeg = ActivateCentralReturnRoute(EntityIndex);
        }
        else
        {
            if (bCompletedReturnLeg)
            {
                ++RouteState.CompletedRoundTrips;
            }
            bPlannedNextLeg = PlanNewDestination(EntityIndex);
        }
        if (!bPlannedNextLeg)
        {
            if (bCompletedReturnLeg)
            {
                --RouteState.CompletedRoundTrips;
            }
            --RouteState.CompletedTrips;
            return NetworkMode == EOpenMassCrowdNetworkMode::CentralCertifiedCache
                ? HoldCentralEntityAtCertifiedPosition(
                    EntityIndex,
                    TEXT("destination_replan_failed"))
                : false;
        }
        ++CompletedTripCount;
        UE_LOG(
            LogTemp,
            Warning,
            TEXT("OPEN_MASS_CROWD_DESTINATION_REACHED entity=%d trips=%d round_trips=%d completed_leg=%s total_completed=%d lane=%d"),
            EntityIndex,
            RouteState.CompletedTrips,
            RouteState.CompletedRoundTrips,
            bCompletedReturnLeg ? TEXT("return") : TEXT("outbound"),
            CompletedTripCount,
            static_cast<int32>(LaneLocation.LaneHandle.Index));
        CurrentRouteIndex = 0;
    }

    const bool bHasNextLane =
        CurrentRouteIndex + 1 < RouteState.LanePath.Num();

    FZoneGraphShortPathRequest PathRequest;
    PathRequest.StartPosition = EntityManager.GetFragmentDataChecked<
        FTransformFragment>(Entity).GetTransform().GetLocation();
    if (NetworkMode == EOpenMassCrowdNetworkMode::CentralCertifiedCache)
    {
        // Investor presentation bands intentionally move the rendered Mass
        // transform sideways from the certified ZoneGraph centreline.  The
        // short-path builder must still start on the authoritative lane
        // position; feeding it the presentation offset makes the coverage
        // clamp correctly reject an otherwise valid route.
        FZoneGraphLaneLocation CertifiedPathStart;
        if (!ZoneGraphSubsystem->CalculateLocationAlongLane(
                LaneLocation.LaneHandle,
                LaneLocation.DistanceAlongLane,
                CertifiedPathStart))
        {
            return HoldCentralEntityAtCertifiedPosition(
                EntityIndex,
                TEXT("certified_path_start_unavailable"));
        }
        PathRequest.StartPosition = CertifiedPathStart.Position;
    }
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
    const float DesiredSpeed = NetworkMode ==
            EOpenMassCrowdNetworkMode::CentralCertifiedCache
        ? GetCentralCruiseSpeedCmPerSecond(EntityIndex)
        : 120.0f + static_cast<float>((EntityIndex * 17) % 36);
    const bool bActivated = UE::MassNavigation::ActivateActionMove(
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
    if (!bActivated)
    {
        return false;
    }

    if (NetworkMode == EOpenMassCrowdNetworkMode::CentralCertifiedCache)
    {
        bool bClampedToCachedGeometry = false;
        if (!ClampCentralShortPathToCachedCoverage(
                PathRequest.bMoveReverse,
                CachedLane,
                MoveTarget,
                ShortPath,
                bClampedToCachedGeometry))
        {
            UE_LOG(
                LogTemp,
                Error,
                TEXT("OPEN_MASS_CROWD_CENTRAL_SHORT_PATH_INVALID entity=%d lane=%d cached_points=%d path_points=%d"),
                EntityIndex,
                LaneLocation.LaneHandle.Index,
                CachedLane.NumPoints,
                ShortPath.NumPoints);
            return HoldCentralEntityAtCertifiedPosition(
                EntityIndex,
                TEXT("short_path_outside_cached_geometry"));
        }

        if (bClampedToCachedGeometry)
        {
            ++CentralShortPathChunkCount;
            if (CentralShortPathChunkCount <= 5 ||
                CentralShortPathChunkCount % 500 == 0)
            {
                UE_LOG(
                    LogTemp,
                    Log,
                    TEXT("OPEN_MASS_CROWD_CENTRAL_SHORT_PATH_CHUNK entity=%d lane=%d chunks=%d cached_start_cm=%.1f cached_end_cm=%.1f request_target_cm=%.1f path_end_cm=%.1f"),
                    EntityIndex,
                    LaneLocation.LaneHandle.Index,
                    CentralShortPathChunkCount,
                    CachedLane.LanePointProgressions[0].Get(),
                    CachedLane.LanePointProgressions[
                        CachedLane.NumPoints - 1].Get(),
                    PathRequest.TargetDistance,
                    ShortPath.Points[
                        ShortPath.NumPoints - 1].DistanceAlongLane.Get());
            }
        }
    }

    return true;
}

float AOpenMassCrowdSpawner::GetCentralCruiseSpeedCmPerSecond(
    const int32 EntityIndex) const
{
    if (!CentralSpawnLanePlan.IsValidIndex(EntityIndex))
    {
        return 120.0f;
    }
    const int32 LaneIndex = CentralSpawnLanePlan[EntityIndex];
    const int32 CandidateReverseLaneIndex =
        RuntimeCentralReverseLaneIndices.IsValidIndex(LaneIndex)
        ? RuntimeCentralReverseLaneIndices[LaneIndex]
        : INDEX_NONE;
    const int32 ReverseLaneIndex =
        RuntimeLaneHandles.IsValidIndex(CandidateReverseLaneIndex)
        ? CandidateReverseLaneIndex
        : LaneIndex;
    const int32 PairKey = FMath::Min(LaneIndex, ReverseLaneIndex);
    return 120.0f + static_cast<float>((PairKey * 17) % 36);
}

bool AOpenMassCrowdSpawner::IsCentralRuntimeLaneAvailable(
    const int32 RuntimeLaneIndex) const
{
    return RuntimeCentralLaneCellIds.IsValidIndex(RuntimeLaneIndex) &&
        !RuntimeUnavailableCentralCellIds.Contains(
            RuntimeCentralLaneCellIds[RuntimeLaneIndex]);
}

bool AOpenMassCrowdSpawner::SetCentralCellRuntimeAvailable(
    const FName CellId,
    const bool bAvailable)
{
    if (NetworkMode != EOpenMassCrowdNetworkMode::CentralCertifiedCache ||
        !RuntimeKnownCentralCellIds.Contains(CellId))
    {
        return false;
    }

    const bool bWasAvailable = !RuntimeUnavailableCentralCellIds.Contains(CellId);
    if (bWasAvailable == bAvailable)
    {
        return true;
    }
    if (bAvailable)
    {
        RuntimeUnavailableCentralCellIds.Remove(CellId);
    }
    else
    {
        RuntimeUnavailableCentralCellIds.Add(CellId);
    }
    ++CentralAvailabilityRevision;
    UE_LOG(
        LogTemp,
        Warning,
        TEXT("OPEN_MASS_CROWD_CENTRAL_CELL_AVAILABILITY cell=%s available=%s revision=%d unavailable_cells=%d"),
        *CellId.ToString(),
        bAvailable ? TEXT("true") : TEXT("false"),
        CentralAvailabilityRevision,
        RuntimeUnavailableCentralCellIds.Num());
    return true;
}

void AOpenMassCrowdSpawner::RecordCentralCellGroundGuard(
    const FName CellId,
    const int32 EntityIndex,
    const bool bSupported)
{
    if (CellId.IsNone() || !RuntimeKnownCentralCellIds.Contains(CellId))
    {
        return;
    }
    if (!bSupported)
    {
        RuntimeCentralCellRecoveryGuardCounts.Remove(CellId);
        if (bInvestorDeliveryDemoEnabled)
        {
            // Investor mode already fail-closes the exact unsupported entity
            // at its last live Cesium point.  Do not let one temporarily
            // unloaded far-tile probe freeze every independently supported
            // pedestrian in the same 150 m cell.  Engineering modes retain
            // the stricter cell-wide closure below.
            RuntimeCentralCellFailedGuardEntities.Remove(CellId);
            return;
        }
        RuntimeCentralCellFailedGuardEntities.FindOrAdd(CellId).Add(EntityIndex);
        SetCentralCellRuntimeAvailable(CellId, false);
        return;
    }
    if (!RuntimeUnavailableCentralCellIds.Contains(CellId))
    {
        RuntimeCentralCellRecoveryGuardCounts.Remove(CellId);
        RuntimeCentralCellFailedGuardEntities.Remove(CellId);
        return;
    }

    if (TSet<int32>* FailedEntities =
        RuntimeCentralCellFailedGuardEntities.Find(CellId))
    {
        FailedEntities->Remove(EntityIndex);
        if (!FailedEntities->IsEmpty())
        {
            RuntimeCentralCellRecoveryGuardCounts.Remove(CellId);
            return;
        }
        RuntimeCentralCellFailedGuardEntities.Remove(CellId);
    }

    int32& RecoveryGuardCount =
        RuntimeCentralCellRecoveryGuardCounts.FindOrAdd(CellId);
    ++RecoveryGuardCount;
    if (RecoveryGuardCount >= CentralCellRecoveryGuardSuccessThreshold)
    {
        RuntimeCentralCellRecoveryGuardCounts.Remove(CellId);
        SetCentralCellRuntimeAvailable(CellId, true);
    }
}

bool AOpenMassCrowdSpawner::HoldCentralEntityAtCertifiedPosition(
    const int32 EntityIndex,
    const TCHAR* Reason)
{
    UMassSpawnerSubsystem* SpawnerSubsystem =
        UWorld::GetSubsystem<UMassSpawnerSubsystem>(GetWorld());
    if (!SpawnerSubsystem ||
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

    FEntityRouteState& RouteState = EntityRouteStates[EntityIndex];
    const bool bWasWaiting = RouteState.bWaitingForAvailableCell;
    RouteState.Reset();
    RouteState.bWaitingForAvailableCell = true;
    RouteState.PlannedAvailabilityRevision = CentralAvailabilityRevision;

    FTransformFragment& TransformFragment =
        EntityManager.GetFragmentDataChecked<FTransformFragment>(Entity);
    FMassZoneGraphLaneLocationFragment& LaneLocation =
        EntityManager.GetFragmentDataChecked<FMassZoneGraphLaneLocationFragment>(Entity);
    if (!LastValidGroundStates.IsValidIndex(EntityIndex) ||
        !LastValidGroundStates[EntityIndex].bValid)
    {
        return false;
    }

    FLastValidGroundState& LastValid = LastValidGroundStates[EntityIndex];
    TransformFragment.SetTransform(LastValid.Transform);
    const FZoneGraphLaneHandle PreviousLane = LaneLocation.LaneHandle;
    LaneLocation.LaneHandle = LastValid.LaneHandle;
    LaneLocation.DistanceAlongLane = LastValid.DistanceAlongLane;
    LaneLocation.LaneLength = LastValid.LaneLength;
    LastValid.bUnsupported = false;
    FMassCrowdLaneTrackingFragment& LaneTracking =
        EntityManager.GetFragmentDataChecked<FMassCrowdLaneTrackingFragment>(Entity);
    if (LaneTracking.TrackedLaneHandle != LastValid.LaneHandle)
    {
        if (UMassCrowdSubsystem* CrowdSubsystem =
            UWorld::GetSubsystem<UMassCrowdSubsystem>(GetWorld()))
        {
            CrowdSubsystem->OnEntityLaneChanged(
                Entity,
                PreviousLane,
                LastValid.LaneHandle);
        }
        LaneTracking.TrackedLaneHandle = LastValid.LaneHandle;
    }

    FMassMoveTargetFragment& MoveTarget =
        EntityManager.GetFragmentDataChecked<FMassMoveTargetFragment>(Entity);
    MoveTarget.CreateNewAction(EMassMovementAction::Stand, *GetWorld());
    MoveTarget.DesiredSpeed = FMassInt16Real(0.0f);
    MoveTarget.Center = TransformFragment.GetTransform().GetLocation();
    MoveTarget.DistanceToGoal = 0.0f;
    EntityManager.GetFragmentDataChecked<FMassZoneGraphShortPathFragment>(Entity).Reset();
    if (FMassVelocityFragment* Velocity =
        EntityManager.GetFragmentDataPtr<FMassVelocityFragment>(Entity))
    {
        Velocity->Value = FVector::ZeroVector;
    }

    if (!bWasWaiting)
    {
        UE_LOG(
            LogTemp,
            Warning,
            TEXT("OPEN_MASS_CROWD_CENTRAL_FAIL_CLOSED entity=%d lane=%d reason=%s availability_revision=%d"),
            EntityIndex,
            LaneLocation.LaneHandle.Index,
            Reason,
            CentralAvailabilityRevision);
    }
    return true;
}

void AOpenMassCrowdSpawner::RefreshCentralUnavailableRoutes()
{
    if (!bCentralAdmissionReleased)
    {
        return;
    }
    const int32 EntityCount = FMath::Min(
        SpawnedEntities.Num(),
        EntityRouteStates.Num());
    if (EntityCount <= 0)
    {
        return;
    }
    bool bRetriedWaitingRoute = false;
    for (int32 Offset = 0; Offset < EntityCount; ++Offset)
    {
        const int32 EntityIndex =
            (CentralWaitingRouteRetryCursor + Offset) % EntityCount;
        FEntityRouteState& RouteState = EntityRouteStates[EntityIndex];
        bool bNeedsAvailabilityReplan =
            RouteState.bWaitingForAvailableCell &&
            RouteState.PlannedAvailabilityRevision != CentralAvailabilityRevision;
        // A route can still fail to plan on the same revision while another
        // cell is recovering.  Once all cells are live, retry one waiting
        // identity per frame.  This closes the permanent-wait hole without
        // creating a burst of 50 A* searches in one presentation frame.
        if (!bNeedsAvailabilityReplan &&
            !bRetriedWaitingRoute &&
            RouteState.bWaitingForAvailableCell &&
            RuntimeUnavailableCentralCellIds.IsEmpty())
        {
            bNeedsAvailabilityReplan = true;
            bRetriedWaitingRoute = true;
            CentralWaitingRouteRetryCursor =
                (EntityIndex + 1) % FMath::Max(1, EntityCount);
        }
        if (!bNeedsAvailabilityReplan && !RouteState.bWaitingForAvailableCell)
        {
            for (int32 PathIndex = FMath::Max(RouteState.CurrentPathIndex, 0);
                 PathIndex < RouteState.LanePath.Num();
                 ++PathIndex)
            {
                if (!IsCentralRuntimeLaneAvailable(
                        RouteState.LanePath[PathIndex].Index))
                {
                    bNeedsAvailabilityReplan = true;
                    break;
                }
            }
        }
        if (bNeedsAvailabilityReplan)
        {
            RequestNextPath(EntityIndex);
        }
    }
}

void AOpenMassCrowdSpawner::ConstrainCentralTransformsToCertifiedLanes()
{
    if (NetworkMode != EOpenMassCrowdNetworkMode::CentralCertifiedCache)
    {
        return;
    }

    UMassSpawnerSubsystem* SpawnerSubsystem =
        UWorld::GetSubsystem<UMassSpawnerSubsystem>(GetWorld());
    UZoneGraphSubsystem* ZoneGraphSubsystem =
        UWorld::GetSubsystem<UZoneGraphSubsystem>(GetWorld());
    if (!SpawnerSubsystem || !ZoneGraphSubsystem)
    {
        return;
    }

    FMassEntityManager& EntityManager = SpawnerSubsystem->GetEntityManagerChecked();
    for (int32 EntityIndex = 0;
         EntityIndex < SpawnedEntities.Num();
         ++EntityIndex)
    {
        const FMassEntityHandle Entity = SpawnedEntities[EntityIndex];
        if (!EntityManager.IsEntityValid(Entity))
        {
            continue;
        }

        FTransformFragment& TransformFragment =
            EntityManager.GetFragmentDataChecked<FTransformFragment>(Entity);
        FMassZoneGraphLaneLocationFragment& LaneLocation =
            EntityManager.GetFragmentDataChecked<FMassZoneGraphLaneLocationFragment>(Entity);
        FMassVelocityFragment* Velocity =
            EntityManager.GetFragmentDataPtr<FMassVelocityFragment>(Entity);
        FMassZoneGraphShortPathFragment* ShortPath =
            EntityManager.GetFragmentDataPtr<
                FMassZoneGraphShortPathFragment>(Entity);

        // A forward Mass action may never move backwards on the same directed
        // ZoneGraph lane. Short-path refreshes near a tiny edge endpoint can
        // otherwise reconstruct progress from an older cached point, producing
        // the observed 49 cm -> 40 cm -> 49 cm oscillation. Restore the last
        // certified forward progress and advance the path cursor by the same
        // amount so the next Mass frame continues from the corrected state.
        if (bInvestorDeliveryDemoEnabled &&
            RuntimeCentralPreviousFrameStates.IsValidIndex(EntityIndex))
        {
            const FLastValidGroundState& PreviousFrameState =
                RuntimeCentralPreviousFrameStates[EntityIndex];
            if (PreviousFrameState.bValid &&
                PreviousFrameState.LaneHandle == LaneLocation.LaneHandle &&
                PreviousFrameState.DistanceAlongLane >
                    LaneLocation.DistanceAlongLane + 0.1f)
            {
                const float RegressedDistanceCm =
                    PreviousFrameState.DistanceAlongLane -
                    LaneLocation.DistanceAlongLane;
                LaneLocation.DistanceAlongLane =
                    PreviousFrameState.DistanceAlongLane;
                if (ShortPath && !ShortPath->IsDone())
                {
                    ShortPath->ProgressDistance += RegressedDistanceCm;
                }
            }
        }

        if constexpr (bUseCentralCertifiedEdgeCirculation)
        {
            FEntityRouteState* RouteState =
                EntityRouteStates.IsValidIndex(EntityIndex)
                ? &EntityRouteStates[EntityIndex]
                : nullptr;
            const FLastValidGroundState* LastValid =
                LastValidGroundStates.IsValidIndex(EntityIndex)
                ? &LastValidGroundStates[EntityIndex]
                : nullptr;
            const bool bNeedsEdgeLivenessAdvance =
                bInvestorDeliveryDemoEnabled &&
                CentralTelemetryStationarySeconds.IsValidIndex(EntityIndex) &&
                CentralTelemetryStationarySeconds[EntityIndex] >=
                    CentralMovementLivenessWindowSeconds &&
                RouteState &&
                !RouteState->bWaitingForAvailableCell &&
                RouteState->LanePath.IsValidIndex(
                    RouteState->CurrentPathIndex) &&
                RouteState->LanePath[RouteState->CurrentPathIndex] ==
                    LaneLocation.LaneHandle &&
                ShortPath && !ShortPath->IsDone() &&
                LastValid && LastValid->bValid &&
                !LastValid->bHasRecoveryProbe &&
                !LastValid->bUnsupported;
            if (bNeedsEdgeLivenessAdvance)
            {
                const bool bFinalRouteLane =
                    RouteState->CurrentPathIndex ==
                        RouteState->LanePath.Num() - 1;
                const float SegmentTargetDistanceCm = bFinalRouteLane
                    ? RouteState->DestinationDistance
                    : LaneLocation.LaneLength - CentralRouteEndpointInsetCm;
                const bool bAtFinalTurnaround =
                    bFinalRouteLane &&
                    LaneLocation.DistanceAlongLane >=
                        SegmentTargetDistanceCm - 0.1f;
                if (bAtFinalTurnaround)
                {
                    const int32 ReverseLaneIndex =
                        RuntimeCentralReverseLaneIndices.IsValidIndex(
                            LaneLocation.LaneHandle.Index)
                        ? RuntimeCentralReverseLaneIndices[
                            LaneLocation.LaneHandle.Index]
                        : INDEX_NONE;
                    float ReverseLaneLength = 0.0f;
                    FZoneGraphLaneLocation CurrentLocation;
                    FZoneGraphLaneLocation ReverseLocation;
                    const float ReverseDistanceCm =
                        RuntimeLaneHandles.IsValidIndex(ReverseLaneIndex) &&
                        ZoneGraphSubsystem->GetLaneLength(
                            RuntimeLaneHandles[ReverseLaneIndex],
                            ReverseLaneLength)
                        ? FMath::Clamp(
                            ReverseLaneLength -
                                LaneLocation.DistanceAlongLane,
                            0.0f,
                            ReverseLaneLength)
                        : -1.0f;
                    UMassCrowdSubsystem* CrowdSubsystem =
                        UWorld::GetSubsystem<UMassCrowdSubsystem>(GetWorld());
                    if (CrowdSubsystem && ReverseDistanceCm >= 0.0f &&
                        ZoneGraphSubsystem->CalculateLocationAlongLane(
                            LaneLocation.LaneHandle,
                            LaneLocation.DistanceAlongLane,
                            CurrentLocation) &&
                        ZoneGraphSubsystem->CalculateLocationAlongLane(
                            RuntimeLaneHandles[ReverseLaneIndex],
                            ReverseDistanceCm,
                            ReverseLocation) &&
                        FVector::Distance(
                            CurrentLocation.Position,
                            ReverseLocation.Position) <= 50.0f)
                    {
                        const FZoneGraphLaneHandle PreviousLaneHandle =
                            LaneLocation.LaneHandle;
                        FMassCrowdLaneTrackingFragment& LaneTracking =
                            EntityManager.GetFragmentDataChecked<
                                FMassCrowdLaneTrackingFragment>(Entity);
                        CrowdSubsystem->OnEntityLaneChanged(
                            Entity,
                            PreviousLaneHandle,
                            RuntimeLaneHandles[ReverseLaneIndex]);
                        LaneTracking.TrackedLaneHandle =
                            RuntimeLaneHandles[ReverseLaneIndex];
                        LaneLocation.LaneHandle =
                            RuntimeLaneHandles[ReverseLaneIndex];
                        LaneLocation.DistanceAlongLane = ReverseDistanceCm;
                        LaneLocation.LaneLength = ReverseLaneLength;
                        if (PlanNewDestination(EntityIndex) &&
                            RequestNextPath(EntityIndex))
                        {
                            ++CentralEdgeLivenessAdvanceCount;
                            continue;
                        }
                    }
                }
                const float DeltaSeconds = FMath::Clamp(
                    static_cast<float>(FApp::GetDeltaTime()),
                    0.0f,
                    0.1f);
                float MaximumAdvanceCm = FMath::Min(
                    GetCentralCruiseSpeedCmPerSecond(EntityIndex) *
                        DeltaSeconds,
                    SegmentTargetDistanceCm -
                        LaneLocation.DistanceAlongLane);
                for (int32 OtherEntityIndex = 0;
                     OtherEntityIndex < SpawnedEntities.Num();
                     ++OtherEntityIndex)
                {
                    if (OtherEntityIndex == EntityIndex)
                    {
                        continue;
                    }
                    const FMassEntityHandle OtherEntity =
                        SpawnedEntities[OtherEntityIndex];
                    if (!EntityManager.IsEntityValid(OtherEntity))
                    {
                        continue;
                    }
                    const FMassZoneGraphLaneLocationFragment* OtherLane =
                        EntityManager.GetFragmentDataPtr<
                            FMassZoneGraphLaneLocationFragment>(OtherEntity);
                    if (!OtherLane ||
                        OtherLane->LaneHandle != LaneLocation.LaneHandle ||
                        GetInvestorPresentationBandIndex(OtherEntityIndex) !=
                            GetInvestorPresentationBandIndex(EntityIndex))
                    {
                        continue;
                    }
                    const float ProgressDifferenceCm =
                        OtherLane->DistanceAlongLane -
                        LaneLocation.DistanceAlongLane;
                    if (ProgressDifferenceCm > 0.0f)
                    {
                        MaximumAdvanceCm = FMath::Min(
                            MaximumAdvanceCm,
                            FMath::Max(
                                0.0f,
                                ProgressDifferenceCm -
                                    CentralMinimumCenterClearanceCm));
                    }
                }
                if (MaximumAdvanceCm >= 0.1f)
                {
                    LaneLocation.DistanceAlongLane += MaximumAdvanceCm;
                    ShortPath->ProgressDistance += MaximumAdvanceCm;
                    ++CentralEdgeLivenessAdvanceCount;
                    if (Velocity)
                    {
                        FZoneGraphLaneLocation RecoveryLocation;
                        if (ZoneGraphSubsystem->CalculateLocationAlongLane(
                                LaneLocation.LaneHandle,
                                LaneLocation.DistanceAlongLane,
                                RecoveryLocation))
                        {
                            Velocity->Value =
                                RecoveryLocation.Tangent.GetSafeNormal() *
                                GetCentralCruiseSpeedCmPerSecond(EntityIndex);
                        }
                    }
                }
            }
        }

        if (!ConstrainCentralEntityTransform(
                EntityIndex,
                TransformFragment,
                LaneLocation,
                Velocity))
        {
            HoldCentralEntityAtCertifiedPosition(
                EntityIndex,
                TEXT("certified_lane_constraint_failed"));
        }
    }
}

bool AOpenMassCrowdSpawner::ConstrainCentralEntityTransform(
    const int32 EntityIndex,
    FTransformFragment& TransformFragment,
    FMassZoneGraphLaneLocationFragment& LaneLocation,
    FMassVelocityFragment* Velocity)
{
    if (NetworkMode != EOpenMassCrowdNetworkMode::CentralCertifiedCache ||
        !LastValidGroundStates.IsValidIndex(EntityIndex) ||
        !EntityRouteStates.IsValidIndex(EntityIndex))
    {
        return false;
    }

    UZoneGraphSubsystem* ZoneGraphSubsystem =
        UWorld::GetSubsystem<UZoneGraphSubsystem>(GetWorld());
    if (!ZoneGraphSubsystem)
    {
        return false;
    }

    FLastValidGroundState& LastValid = LastValidGroundStates[EntityIndex];
    const FEntityRouteState& RouteState = EntityRouteStates[EntityIndex];
    const auto RestoreLastLiveTransform = [&]()
    {
        if (LastValid.bValid)
        {
            TransformFragment.SetTransform(LastValid.Transform);
        }
    };

    // A failed live-streaming guard owns the entity until its cell passes the
    // recovery quorum. Re-exposing a cached forward step here would defeat
    // fail-closed behavior.
    if (RouteState.bWaitingForAvailableCell || LastValid.bHasRecoveryProbe)
    {
        RestoreLastLiveTransform();
        return LastValid.bValid;
    }

    const int32 RuntimeLaneIndex = LaneLocation.LaneHandle.Index;
    if (!IsCentralRuntimeLaneAvailable(RuntimeLaneIndex))
    {
        RestoreLastLiveTransform();
        return false;
    }

    float LaneLength = LaneLocation.LaneLength;
    if (!FMath::IsFinite(LaneLength) || LaneLength <= KINDA_SMALL_NUMBER)
    {
        if (!ZoneGraphSubsystem->GetLaneLength(
                LaneLocation.LaneHandle,
                LaneLength) ||
            LaneLength <= KINDA_SMALL_NUMBER)
        {
            RestoreLastLiveTransform();
            return false;
        }
        LaneLocation.LaneLength = LaneLength;
    }

    LaneLocation.DistanceAlongLane = FMath::Clamp(
        LaneLocation.DistanceAlongLane,
        0.0f,
        LaneLength);
    FZoneGraphLaneLocation CertifiedLocation;
    if (!ZoneGraphSubsystem->CalculateLocationAlongLane(
            LaneLocation.LaneHandle,
            LaneLocation.DistanceAlongLane,
            CertifiedLocation))
    {
        RestoreLastLiveTransform();
        return false;
    }

    // Central runtime lanes preserve every accepted 10 cm certification sample
    // and already contain the single +2 cm clearance. Interpolate the complete
    // transform directly; do not apply LaneHeightOffset a second time.
    FVector CertifiedVisualPosition = CertifiedLocation.Position;
    if (bInvestorDeliveryDemoEnabled &&
        CentralPresentationOffsetValid.IsValidIndex(EntityIndex) &&
        CentralPresentationOffsetValid[EntityIndex] != 0)
    {
        // The band is never speculative: CorrectMassGrounding sets this bit
        // only after an exact-XY Cesium first-hit succeeds for the offset.
        FZoneGraphLaneLocation PresentationLocation;
        const float PresentationDistance = FMath::Clamp(
            LaneLocation.DistanceAlongLane +
                GetInvestorPresentationPhaseOffsetCm(EntityIndex),
            0.0f,
            LaneLength);
        if (ZoneGraphSubsystem->CalculateLocationAlongLane(
                LaneLocation.LaneHandle,
                PresentationDistance,
                PresentationLocation))
        {
            CertifiedVisualPosition = GetInvestorPresentationPosition(
                PresentationLocation,
                EntityIndex);
        }
    }
    if (LastValid.bValid && !LastValid.bUnsupported)
    {
        FZoneGraphLaneLocation CachedLastValidLocation;
        if (ZoneGraphSubsystem->CalculateLocationAlongLane(
                LastValid.LaneHandle,
                FMath::Clamp(
                    LastValid.DistanceAlongLane,
                    0.0f,
                    LastValid.LaneLength),
                CachedLastValidLocation))
        {
            // Cesium may refine to a slightly different vertical LOD after the
            // cache was built. Carry only the latest live exact-XY Z residual
            // between guards; XY never borrows a neighbouring footprint.
            const float LiveHeightResidual = FMath::Clamp(
                LastValid.Transform.GetLocation().Z -
                    CachedLastValidLocation.Position.Z,
                -GroundTolerance,
                GroundTolerance);
            CertifiedVisualPosition.Z += LiveHeightResidual;
        }
    }
    const FTransform CertifiedTransform(
        CertifiedLocation.Tangent.ToOrientationQuat(),
        CertifiedVisualPosition);
    TransformFragment.SetTransform(CertifiedTransform);
    if (Velocity)
    {
        // Removing avoidance's lateral displacement must not discard forward
        // speed, which drives locomotion and movement telemetry.
        const FVector LaneTangent =
            CertifiedLocation.Tangent.GetSafeNormal();
        const float ForwardSpeed = FVector::DotProduct(
            Velocity->Value,
            LaneTangent);
        Velocity->Value =
            LaneTangent * FMath::Max(ForwardSpeed, 0.0f);
    }

    // Deliberately do not update LastValid here. This transform is backed by
    // the strict offline cache, while LastValid is the rollback point of the
    // most recent successful *live* exact-XY Cesium query.
    return true;
}

void AOpenMassCrowdSpawner::RecordCentralFrameTime(const float DeltaSeconds)
{
    UWorld* World = GetWorld();
    if (!World || DeltaSeconds <= 0.0f || !FMath::IsFinite(DeltaSeconds))
    {
        return;
    }

    const double WorldTimeSeconds = World->GetRealTimeSeconds();
    if (!FMath::IsFinite(WorldTimeSeconds))
    {
        return;
    }

    // A PIE/world-time restart invalidates timestamps retained by an unusual
    // in-place world transition. Fail cleanly rather than publishing a mixed
    // percentile window.
    if (!CentralFrameTimeSamples.IsEmpty() &&
        WorldTimeSeconds < CentralFrameTimeSamples.Last().WorldTimeSeconds)
    {
        CentralFrameTimeSamples.Reset();
        CentralFrameTimeFirstSampleIndex = 0;
    }

    const double OldestAllowedTime =
        WorldTimeSeconds - CentralFrameTimeRetentionWindowSeconds;
    while (CentralFrameTimeFirstSampleIndex < CentralFrameTimeSamples.Num() &&
        CentralFrameTimeSamples[CentralFrameTimeFirstSampleIndex].WorldTimeSeconds <
            OldestAllowedTime)
    {
        ++CentralFrameTimeFirstSampleIndex;
    }
    while (CentralFrameTimeSamples.Num() - CentralFrameTimeFirstSampleIndex >=
        CentralMaximumFrameTimeSamples)
    {
        ++CentralFrameTimeFirstSampleIndex;
    }

    FCentralFrameTimeSample& Sample = CentralFrameTimeSamples.AddDefaulted_GetRef();
    Sample.WorldTimeSeconds = WorldTimeSeconds;
    Sample.FrameTimeMilliseconds = DeltaSeconds * 1000.0f;

    // Removing old samples every frame would shift thousands of values. Keep
    // a logical head and compact only in bounded chunks.
    if (CentralFrameTimeFirstSampleIndex >=
            CentralFrameTimeCompactionThreshold &&
        CentralFrameTimeFirstSampleIndex * 2 >=
            CentralFrameTimeSamples.Num())
    {
        CentralFrameTimeSamples.RemoveAt(
            0,
            CentralFrameTimeFirstSampleIndex,
            EAllowShrinking::No);
        CentralFrameTimeFirstSampleIndex = 0;
    }
}

void AOpenMassCrowdSpawner::RecordCentralTelemetry(const bool bForceLog)
{
    const bool bPeriodicHealthSample =
        CentralTelemetrySampleAccumulator >=
        CentralTelemetrySampleIntervalSeconds;
    // Reservation and certified-transform processors still enforce spacing on
    // every Mass frame. Investor mode samples the diagnostic all-pairs scan at
    // the telemetry cadence instead of rebuilding it on every presentation
    // frame; engineering gates retain the stricter every-frame evidence path.
    const bool bCollisionHealthSample =
        !bInvestorDeliveryDemoEnabled || bPeriodicHealthSample || bForceLog;
    if (!bPeriodicHealthSample && !bCollisionHealthSample)
    {
        return;
    }

    UMassSpawnerSubsystem* SpawnerSubsystem =
        UWorld::GetSubsystem<UMassSpawnerSubsystem>(GetWorld());
    if (!SpawnerSubsystem)
    {
        return;
    }
    FMassEntityManager& EntityManager = SpawnerSubsystem->GetEntityManagerChecked();

    const float HealthSampleSeconds = bPeriodicHealthSample
        ? FMath::Max(
            CentralTelemetrySampleAccumulator,
            CentralTelemetrySampleIntervalSeconds)
        : 0.0f;
    if (bPeriodicHealthSample)
    {
        CentralTelemetrySampleAccumulator = FMath::Fmod(
            CentralTelemetrySampleAccumulator,
            CentralTelemetrySampleIntervalSeconds);
    }

    if (CentralTelemetryLastCertifiedPositions.Num() < SpawnedEntities.Num())
    {
        CentralTelemetryLastCertifiedPositions.SetNumZeroed(
            SpawnedEntities.Num());
        CentralTelemetryStationarySeconds.SetNumZeroed(
            SpawnedEntities.Num());
        CentralTelemetryPositionValid.SetNumZeroed(
            SpawnedEntities.Num());
    }

    int32 SimulatedCount = 0;
    int32 RepresentedCount = 0;
    CentralHighActorRepresentationCount = 0;
    CentralLowActorRepresentationCount = 0;
    CentralVATRepresentationCount = 0;

    if (bPeriodicHealthSample)
    {
        CentralExpectedMovingEntityCount = 0;
        CentralMovingEntityCount = 0;
        CentralStuckEntityCount = 0;
    }
    if (bCollisionHealthSample)
    {
        CentralSevereOverlapPairCount = 0;
        CentralSevereOverlapAgentCount = 0;
        CentralMinimumEntityCenterDistanceCm = -1.0f;
        ++CentralTelemetryObservationCount;
    }

    TArray<FVector> CertifiedPositions;
    TArray<int32> CertifiedEntityIndices;
    FString StuckEntityTelemetry;
    struct FCentralStuckRecoveryCandidate
    {
        FVector Position = FVector::ZeroVector;
        int32 EntityIndex = INDEX_NONE;
        int32 CurrentLaneIndex = INDEX_NONE;
        int32 NextLaneIndex = INDEX_NONE;
        int32 WaitResourceIndex = INDEX_NONE;
        float ProgressCm = 0.0f;
        float StationarySeconds = 0.0f;
        bool bCrossedRecoveryBoundary = false;
    };
    TArray<FCentralStuckRecoveryCandidate> StuckRecoveryCandidates;
    TArray<int32> EdgePathRefreshEntityIndices;
    if (bCollisionHealthSample)
    {
        CertifiedPositions.Reserve(SpawnedEntities.Num());
        CertifiedEntityIndices.Reserve(SpawnedEntities.Num());
    }

    for (int32 EntityIndex = 0;
         EntityIndex < SpawnedEntities.Num();
         ++EntityIndex)
    {
        const FMassEntityHandle Entity = SpawnedEntities[EntityIndex];
        if (!EntityManager.IsEntityValid(Entity))
        {
            continue;
        }
        const FMassRepresentationFragment* Representation =
            EntityManager.GetFragmentDataPtr<FMassRepresentationFragment>(Entity);
        if (Representation)
        {
            switch (Representation->CurrentRepresentation)
            {
            case EMassRepresentationType::HighResSpawnedActor:
                ++RepresentedCount;
                ++CentralHighActorRepresentationCount;
                break;
            case EMassRepresentationType::LowResSpawnedActor:
                ++RepresentedCount;
                ++CentralLowActorRepresentationCount;
                break;
            case EMassRepresentationType::StaticMeshInstance:
                ++RepresentedCount;
                ++CentralVATRepresentationCount;
                break;
            case EMassRepresentationType::None:
            default:
                break;
            }
        }

        const FTransformFragment* TransformFragment =
            EntityManager.GetFragmentDataPtr<FTransformFragment>(Entity);
        if (!TransformFragment)
        {
            CentralInvalidPositionObservationCount =
                CentralInvalidPositionObservationCount >= MAX_int32
                ? MAX_int32
                : CentralInvalidPositionObservationCount + 1;
            CentralTelemetryPositionValid[EntityIndex] = 0;
            CentralTelemetryStationarySeconds[EntityIndex] = 0.0f;
            continue;
        }

        // Central's authoritative transform is clamped every PostUpdateWork
        // tick to the complete offline-certified 10 cm directional support
        // track. A
        // fail-closed entity has already had this fragment restored to the
        // last live exact-XY point, so this one source measures both smooth
        // healthy motion and honest streaming holds.
        const FVector CertifiedPosition =
            TransformFragment->GetTransform().GetLocation();
        if (!FMath::IsFinite(CertifiedPosition.X) ||
            !FMath::IsFinite(CertifiedPosition.Y) ||
            !FMath::IsFinite(CertifiedPosition.Z))
        {
            CentralInvalidPositionObservationCount =
                CentralInvalidPositionObservationCount >= MAX_int32
                ? MAX_int32
                : CentralInvalidPositionObservationCount + 1;
            CentralTelemetryPositionValid[EntityIndex] = 0;
            CentralTelemetryStationarySeconds[EntityIndex] = 0.0f;
            continue;
        }
        ++SimulatedCount;
        if (bCollisionHealthSample)
        {
            CertifiedPositions.Add(CertifiedPosition);
            CertifiedEntityIndices.Add(EntityIndex);
        }
        if (!bPeriodicHealthSample)
        {
            continue;
        }

        const FLastValidGroundState* LastValid =
            LastValidGroundStates.IsValidIndex(EntityIndex)
                ? &LastValidGroundStates[EntityIndex]
                : nullptr;
        if (!LastValid || !LastValid->bValid)
        {
            CentralTelemetryPositionValid[EntityIndex] = 0;
            CentralTelemetryStationarySeconds[EntityIndex] = 0.0f;
            continue;
        }

        const FEntityRouteState* RouteState =
            EntityRouteStates.IsValidIndex(EntityIndex)
                ? &EntityRouteStates[EntityIndex]
                : nullptr;
        const bool bExpectedToMove = RouteState &&
            !RouteState->bWaitingForAvailableCell &&
            RouteState->CurrentPathIndex != INDEX_NONE &&
            !RouteState->LanePath.IsEmpty() &&
            !LastValid->bHasRecoveryProbe &&
            !LastValid->bUnsupported;
        if (bExpectedToMove)
        {
            ++CentralExpectedMovingEntityCount;
        }

        const FMassVelocityFragment* Velocity =
            EntityManager.GetFragmentDataPtr<FMassVelocityFragment>(Entity);
        const bool bHasMovingVelocity = Velocity &&
            Velocity->Value.Size2D() >=
                CentralMovingSpeedThresholdCmPerSecond;
        bool bMoved = false;
        const float PreviousStationarySeconds =
            CentralTelemetryStationarySeconds[EntityIndex];
        if (CentralTelemetryPositionValid[EntityIndex] != 0)
        {
            const float ObservedSpeed = FVector::Dist(
                CertifiedPosition,
                CentralTelemetryLastCertifiedPositions[EntityIndex]) /
                FMath::Max(HealthSampleSeconds, UE_SMALL_NUMBER);
            // Once a certified-position baseline exists, only observed
            // certified displacement is authoritative. Residual Mass velocity
            // is diagnostic only and must not mask a stationary supported
            // transform.
            bMoved = ObservedSpeed >=
                CentralMovingSpeedThresholdCmPerSecond;
            if (bExpectedToMove)
            {
            if (bMoved)
            {
                CentralTelemetryStationarySeconds[EntityIndex] = 0.0f;
                }
                else
                {
                    CentralTelemetryStationarySeconds[EntityIndex] +=
                        HealthSampleSeconds;
                }
            }

            if (!bMoved && bUseCentralCertifiedEdgeCirculation &&
                PreviousStationarySeconds <=
                    CentralEdgePathRefreshThresholdSeconds &&
                CentralTelemetryStationarySeconds[EntityIndex] >
                    CentralEdgePathRefreshThresholdSeconds)
            {
                EdgePathRefreshEntityIndices.Add(EntityIndex);
            }
        }
        else
        {
            // The first observation has no displacement baseline. Velocity is
            // used only for this one sample; every later result is certified
            // position displacement.
            bMoved = bHasMovingVelocity;
            CentralTelemetryStationarySeconds[EntityIndex] = 0.0f;
        }

        if (!bExpectedToMove)
        {
            // Fail-closed waits are intentional safety behavior, not routing
            // stalls. Reset so a recovered entity receives a fresh five-second
            // observation period.
            CentralTelemetryStationarySeconds[EntityIndex] = 0.0f;
        }
        else
        {
            if (bMoved ||
                CentralTelemetryStationarySeconds[EntityIndex] <=
                    CentralMovementLivenessWindowSeconds)
            {
                ++CentralMovingEntityCount;
            }
            if (CentralTelemetryStationarySeconds[EntityIndex] >
                CentralStuckThresholdSeconds)
            {
                ++CentralStuckEntityCount;

                const FMassZoneGraphLaneLocationFragment* StuckLane =
                    EntityManager.GetFragmentDataPtr<
                        FMassZoneGraphLaneLocationFragment>(Entity);
                const int32 StuckLaneIndex = StuckLane
                    ? StuckLane->LaneHandle.Index
                    : INDEX_NONE;
                const FName StuckLaneId =
                    RuntimeCentralLaneIds.IsValidIndex(StuckLaneIndex)
                    ? RuntimeCentralLaneIds[StuckLaneIndex]
                    : NAME_None;
                const FEntityRouteState& StuckRoute =
                    EntityRouteStates[EntityIndex];
                int32 NextLaneIndex = INDEX_NONE;
                if (StuckRoute.LanePath.IsValidIndex(
                        StuckRoute.CurrentPathIndex + 1))
                {
                    NextLaneIndex = StuckRoute.LanePath[
                        StuckRoute.CurrentPathIndex + 1].Index;
                }
                const int32 WaitResourceIndex =
                    RuntimeCentralLocalConflictWaitResourceIndices.
                        IsValidIndex(EntityIndex)
                    ? RuntimeCentralLocalConflictWaitResourceIndices[
                          EntityIndex]
                    : INDEX_NONE;
                const float WaitSeconds =
                    RuntimeCentralLocalConflictWaitSeconds.IsValidIndex(
                        EntityIndex)
                    ? RuntimeCentralLocalConflictWaitSeconds[EntityIndex]
                    : 0.0f;

                // Local reservations normally drain a contested node without
                // intervention.  A completely stationary pair cannot accrue
                // scheduler wait age, however: neither member becomes a move
                // candidate.  Record such pairs here and, once per five-second
                // boundary, deterministically replan only the higher stable
                // entity index.  The lower index keeps its route and therefore
                // breaks symmetry without teleporting either pedestrian or
                // relaxing the 55 cm reservation contract.
                const int32 PreviousRecoveryRound = FMath::FloorToInt(
                    PreviousStationarySeconds /
                    CentralStuckThresholdSeconds);
                const int32 CurrentRecoveryRound = FMath::FloorToInt(
                    CentralTelemetryStationarySeconds[EntityIndex] /
                    CentralStuckThresholdSeconds);
                FCentralStuckRecoveryCandidate& RecoveryCandidate =
                    StuckRecoveryCandidates.AddDefaulted_GetRef();
                RecoveryCandidate.Position = CertifiedPosition;
                RecoveryCandidate.EntityIndex = EntityIndex;
                RecoveryCandidate.CurrentLaneIndex = StuckLaneIndex;
                RecoveryCandidate.NextLaneIndex = NextLaneIndex;
                RecoveryCandidate.WaitResourceIndex = WaitResourceIndex;
                RecoveryCandidate.ProgressCm = StuckLane
                    ? StuckLane->DistanceAlongLane
                    : 0.0f;
                RecoveryCandidate.StationarySeconds =
                    CentralTelemetryStationarySeconds[EntityIndex];
                RecoveryCandidate.bCrossedRecoveryBoundary =
                    (PreviousStationarySeconds <= CentralStuckThresholdSeconds &&
                     CentralTelemetryStationarySeconds[EntityIndex] >
                         CentralStuckThresholdSeconds) ||
                    (CurrentRecoveryRound > PreviousRecoveryRound &&
                     CurrentRecoveryRound >= 1);
                if (!StuckEntityTelemetry.IsEmpty())
                {
                    StuckEntityTelemetry += TEXT("|");
                }
                StuckEntityTelemetry += FString::Printf(
                    TEXT("e%d@l%d[%s]:p%.1f->l%d:r%d:wait%.2f:path%d/%d"),
                    EntityIndex,
                    StuckLaneIndex,
                    *StuckLaneId.ToString(),
                    StuckLane ? StuckLane->DistanceAlongLane : -1.0f,
                    NextLaneIndex,
                    WaitResourceIndex,
                    WaitSeconds,
                    StuckRoute.CurrentPathIndex,
                    StuckRoute.LanePath.Num());
            }
        }

        CentralTelemetryLastCertifiedPositions[EntityIndex] = CertifiedPosition;
        CentralTelemetryPositionValid[EntityIndex] = 1;
    }

    for (const int32 EntityIndex : EdgePathRefreshEntityIndices)
    {
        const bool bRefreshed = RequestNextPath(EntityIndex);
        UE_LOG(
            LogTemp,
            Verbose,
            TEXT("OPEN_MASS_CROWD_CENTRAL_PATH_RENEW entity=%d refreshed=%s threshold_s=%.2f policy=exact_route_no_teleport"),
            EntityIndex,
            bRefreshed ? TEXT("true") : TEXT("false"),
            CentralEdgePathRefreshThresholdSeconds);
    }

    // A five-second certified-displacement stall means reissuing the same
    // short path is no longer useful. Turn the pedestrian around in place by
    // mapping its progress to the exact reverse certified lane. The live point
    // must remain within 50 cm, so this cannot teleport to another pavement.
    // It breaks a cyclic same-band queue while preserving identity and ground
    // provenance.
    if (bUseCentralCertifiedEdgeCirculation &&
        !StuckRecoveryCandidates.IsEmpty())
    {
        UZoneGraphSubsystem* ZoneGraphSubsystem =
            UWorld::GetSubsystem<UZoneGraphSubsystem>(GetWorld());
        UMassCrowdSubsystem* CrowdSubsystem =
            UWorld::GetSubsystem<UMassCrowdSubsystem>(GetWorld());
        for (const FCentralStuckRecoveryCandidate& RecoveryCandidate :
             StuckRecoveryCandidates)
        {
            if (!RecoveryCandidate.bCrossedRecoveryBoundary)
            {
                continue;
            }
            bool bTurnedInPlace = false;
            const int32 EntityIndex = RecoveryCandidate.EntityIndex;
            if (ZoneGraphSubsystem && CrowdSubsystem &&
                SpawnedEntities.IsValidIndex(EntityIndex) &&
                EntityManager.IsEntityValid(SpawnedEntities[EntityIndex]))
            {
                const FMassEntityHandle Entity = SpawnedEntities[EntityIndex];
                FMassZoneGraphLaneLocationFragment& LaneLocation =
                    EntityManager.GetFragmentDataChecked<
                        FMassZoneGraphLaneLocationFragment>(Entity);
                const int32 ReverseLaneIndex =
                    RuntimeCentralReverseLaneIndices.IsValidIndex(
                        LaneLocation.LaneHandle.Index)
                    ? RuntimeCentralReverseLaneIndices[
                          LaneLocation.LaneHandle.Index]
                    : INDEX_NONE;
                float ReverseLaneLength = 0.0f;
                FZoneGraphLaneLocation CurrentLocation;
                FZoneGraphLaneLocation ReverseLocation;
                const float ReverseDistanceCm =
                    RuntimeLaneHandles.IsValidIndex(ReverseLaneIndex) &&
                    ZoneGraphSubsystem->GetLaneLength(
                        RuntimeLaneHandles[ReverseLaneIndex],
                        ReverseLaneLength)
                    ? FMath::Clamp(
                          ReverseLaneLength -
                              LaneLocation.DistanceAlongLane,
                          0.0f,
                          ReverseLaneLength)
                    : -1.0f;
                if (ReverseDistanceCm >= 0.0f &&
                    ZoneGraphSubsystem->CalculateLocationAlongLane(
                        LaneLocation.LaneHandle,
                        LaneLocation.DistanceAlongLane,
                        CurrentLocation) &&
                    ZoneGraphSubsystem->CalculateLocationAlongLane(
                        RuntimeLaneHandles[ReverseLaneIndex],
                        ReverseDistanceCm,
                        ReverseLocation) &&
                    FVector::Distance(
                        CurrentLocation.Position,
                        ReverseLocation.Position) <= 50.0f)
                {
                    const FZoneGraphLaneHandle PreviousLaneHandle =
                        LaneLocation.LaneHandle;
                    FMassCrowdLaneTrackingFragment& LaneTracking =
                        EntityManager.GetFragmentDataChecked<
                            FMassCrowdLaneTrackingFragment>(Entity);
                    CrowdSubsystem->OnEntityLaneChanged(
                        Entity,
                        PreviousLaneHandle,
                        RuntimeLaneHandles[ReverseLaneIndex]);
                    LaneTracking.TrackedLaneHandle =
                        RuntimeLaneHandles[ReverseLaneIndex];
                    LaneLocation.LaneHandle =
                        RuntimeLaneHandles[ReverseLaneIndex];
                    LaneLocation.DistanceAlongLane = ReverseDistanceCm;
                    LaneLocation.LaneLength = ReverseLaneLength;
                    if (LastValidGroundStates.IsValidIndex(EntityIndex) &&
                        LastValidGroundStates[EntityIndex].bValid)
                    {
                        FLastValidGroundState& LastValid =
                            LastValidGroundStates[EntityIndex];
                        LastValid.LaneHandle = LaneLocation.LaneHandle;
                        LastValid.DistanceAlongLane = ReverseDistanceCm;
                        LastValid.LaneLength = ReverseLaneLength;
                    }
                    bTurnedInPlace = PlanNewDestination(EntityIndex) &&
                        RequestNextPath(EntityIndex);
                    if (bTurnedInPlace)
                    {
                        ++CentralStallRecoveryReplanCount;
                        ++CentralEdgeLivenessAdvanceCount;
                    }
                }
            }
            const bool bRefreshed = bTurnedInPlace ||
                RequestNextPath(EntityIndex);
            UE_LOG(
                LogTemp,
                Warning,
                TEXT("OPEN_MASS_CROWD_CENTRAL_PATH_REFRESH entity=%d lane=%d stationary_s=%.2f refreshed=%s turned_in_place=%s policy=reverse_certified_point_no_teleport"),
                EntityIndex,
                RecoveryCandidate.CurrentLaneIndex,
                RecoveryCandidate.StationarySeconds,
                bRefreshed ? TEXT("true") : TEXT("false"),
                bTurnedInPlace ? TEXT("true") : TEXT("false"));
        }
    }

    // A successful action activation is not proof of progress. Multi-junction
    // investor routes keep their certified-displacement timer until the Mass
    // transform actually advances; at each five-second boundary, select one
    // deterministic member of a blocked queue and route it away from the
    // contested next lane. Engineering gates retain their established policy.
    const bool bEnableTelemetryStuckRecoveryReplans =
        bInvestorDeliveryDemoEnabled &&
        !bUseCentralCertifiedEdgeCirculation;
    if (bEnableTelemetryStuckRecoveryReplans &&
        !StuckRecoveryCandidates.IsEmpty())
    {
        TSet<int32> RecoveryEntityIndices;
        const float ProximityConflictDistanceSquared = FMath::Square(
            CentralMinimumCenterClearanceCm);
        for (const FCentralStuckRecoveryCandidate& Trigger :
             StuckRecoveryCandidates)
        {
            if (!Trigger.bCrossedRecoveryBoundary)
            {
                continue;
            }

            int32 SelectedEntityIndex = Trigger.EntityIndex;
            float SelectedSameLaneProgressCm = Trigger.ProgressCm;
            bool bSelectedFromSameLaneQueue = false;
            for (const FCentralStuckRecoveryCandidate& Peer :
                 StuckRecoveryCandidates)
            {
                if (Peer.EntityIndex == Trigger.EntityIndex ||
                    Trigger.CurrentLaneIndex == INDEX_NONE ||
                    Trigger.CurrentLaneIndex != Peer.CurrentLaneIndex)
                {
                    continue;
                }
                bSelectedFromSameLaneQueue = true;
                if (Peer.ProgressCm > SelectedSameLaneProgressCm ||
                    (FMath::IsNearlyEqual(
                         Peer.ProgressCm,
                         SelectedSameLaneProgressCm) &&
                     Peer.EntityIndex < SelectedEntityIndex))
                {
                    SelectedEntityIndex = Peer.EntityIndex;
                    SelectedSameLaneProgressCm = Peer.ProgressCm;
                }
            }
            // A shared concrete next lane is the strongest evidence of a merge
            // deadlock.  A shared local resource covers crossings; exact
            // proximity covers final-lane branch pairs which have no next lane.
            for (const FCentralStuckRecoveryCandidate& Peer :
                 StuckRecoveryCandidates)
            {
                if (bSelectedFromSameLaneQueue)
                {
                    break;
                }
                const bool bSharedCurrentLane =
                    Peer.EntityIndex != Trigger.EntityIndex &&
                    Trigger.CurrentLaneIndex != INDEX_NONE &&
                    Trigger.CurrentLaneIndex == Peer.CurrentLaneIndex;
                if (bSharedCurrentLane)
                {
                    // Only the front of a same-lane queue can release every
                    // follower. Replanning followers merely churns their paths
                    // while the physical blocker remains at the lane end.
                    if (!bSelectedFromSameLaneQueue ||
                        Peer.ProgressCm > SelectedSameLaneProgressCm ||
                        (FMath::IsNearlyEqual(
                             Peer.ProgressCm,
                             SelectedSameLaneProgressCm) &&
                         Peer.EntityIndex < SelectedEntityIndex))
                    {
                        SelectedEntityIndex = Peer.EntityIndex;
                        SelectedSameLaneProgressCm = Peer.ProgressCm;
                    }
                    bSelectedFromSameLaneQueue = true;
                    continue;
                }
                const bool bSharedNextLane =
                    Trigger.NextLaneIndex != INDEX_NONE &&
                    Trigger.NextLaneIndex == Peer.NextLaneIndex;
                const bool bSharedWaitResource =
                    Trigger.WaitResourceIndex != INDEX_NONE &&
                    Trigger.WaitResourceIndex == Peer.WaitResourceIndex;
                const bool bProximityConflict = FVector::DistSquared(
                    Trigger.Position,
                    Peer.Position) < ProximityConflictDistanceSquared;
                if (!bSelectedFromSameLaneQueue &&
                    (bSharedNextLane || bSharedWaitResource ||
                    bProximityConflict)
                    )
                {
                    SelectedEntityIndex = FMath::Max(
                        SelectedEntityIndex,
                        Peer.EntityIndex);
                }
            }
            RecoveryEntityIndices.Add(SelectedEntityIndex);
        }

        TArray<int32> OrderedRecoveryEntityIndices =
            RecoveryEntityIndices.Array();
        OrderedRecoveryEntityIndices.Sort();
        for (const int32 RecoveryEntityIndex :
             OrderedRecoveryEntityIndices)
        {
            const FCentralStuckRecoveryCandidate* RecoveryCandidate =
                StuckRecoveryCandidates.FindByPredicate(
                    [RecoveryEntityIndex](
                        const FCentralStuckRecoveryCandidate& Candidate)
                    {
                        return Candidate.EntityIndex == RecoveryEntityIndex;
                    });
            if (!RecoveryCandidate)
            {
                continue;
            }

            TSet<int32> ForbiddenLaneIndices;
            if (RecoveryCandidate->NextLaneIndex != INDEX_NONE)
            {
                ForbiddenLaneIndices.Add(RecoveryCandidate->NextLaneIndex);
            }
            QueueCentralConflictReplan(
                RecoveryEntityIndex,
                ForbiddenLaneIndices);
            ++CentralStallRecoveryReplanCount;
            UE_LOG(
                LogTemp,
                Warning,
                TEXT("OPEN_MASS_CROWD_CENTRAL_STUCK_RECOVERY entity=%d stationary_s=%.2f next_lane=%d wait_resource=%d forbidden_lanes=%d policy=front_of_same_lane_else_stable_high_replan"),
                RecoveryEntityIndex,
                RecoveryCandidate->StationarySeconds,
                RecoveryCandidate->NextLaneIndex,
                RecoveryCandidate->WaitResourceIndex,
                ForbiddenLaneIndices.Num());
        }
    }

    if (bCollisionHealthSample && CertifiedPositions.Num() >= 2)
    {
        TArray<uint8> SevereOverlapAgents;
        SevereOverlapAgents.SetNumZeroed(CertifiedPositions.Num());
        float MinimumSquaredDistance = TNumericLimits<float>::Max();
        const float SevereOverlapSquaredDistance =
            FMath::Square(CentralSevereOverlapDistanceCm);
        for (int32 FirstIndex = 0;
             FirstIndex < CertifiedPositions.Num() - 1;
             ++FirstIndex)
        {
            for (int32 SecondIndex = FirstIndex + 1;
                 SecondIndex < CertifiedPositions.Num();
                 ++SecondIndex)
            {
                const float SquaredDistance = FVector::DistSquared(
                    CertifiedPositions[FirstIndex],
                    CertifiedPositions[SecondIndex]);
                MinimumSquaredDistance = FMath::Min(
                    MinimumSquaredDistance,
                    SquaredDistance);
                if (SquaredDistance < SevereOverlapSquaredDistance)
                {
                    ++CentralSevereOverlapPairCount;
                    SevereOverlapAgents[FirstIndex] = 1;
                    SevereOverlapAgents[SecondIndex] = 1;

                    const int32 FirstEntityIndex =
                        CertifiedEntityIndices.IsValidIndex(FirstIndex)
                            ? CertifiedEntityIndices[FirstIndex]
                            : INDEX_NONE;
                    const int32 SecondEntityIndex =
                        CertifiedEntityIndices.IsValidIndex(SecondIndex)
                            ? CertifiedEntityIndices[SecondIndex]
                            : INDEX_NONE;
                    int32 FirstLaneIndex = INDEX_NONE;
                    int32 SecondLaneIndex = INDEX_NONE;
                    float FirstLaneDistance = -1.0f;
                    float SecondLaneDistance = -1.0f;
                    if (SpawnedEntities.IsValidIndex(FirstEntityIndex))
                    {
                        if (const FMassZoneGraphLaneLocationFragment* FirstLane =
                                EntityManager.GetFragmentDataPtr<
                                    FMassZoneGraphLaneLocationFragment>(
                                    SpawnedEntities[FirstEntityIndex]))
                        {
                            FirstLaneIndex = FirstLane->LaneHandle.Index;
                            FirstLaneDistance = FirstLane->DistanceAlongLane;
                        }
                    }
                    if (SpawnedEntities.IsValidIndex(SecondEntityIndex))
                    {
                        if (const FMassZoneGraphLaneLocationFragment* SecondLane =
                                EntityManager.GetFragmentDataPtr<
                                    FMassZoneGraphLaneLocationFragment>(
                                    SpawnedEntities[SecondEntityIndex]))
                        {
                            SecondLaneIndex = SecondLane->LaneHandle.Index;
                            SecondLaneDistance = SecondLane->DistanceAlongLane;
                        }
                    }
                    const FString FirstLaneId =
                        RuntimeCentralLaneIds.IsValidIndex(FirstLaneIndex)
                        ? RuntimeCentralLaneIds[FirstLaneIndex].ToString()
                        : TEXT("invalid");
                    const FString SecondLaneId =
                        RuntimeCentralLaneIds.IsValidIndex(SecondLaneIndex)
                        ? RuntimeCentralLaneIds[SecondLaneIndex].ToString()
                        : TEXT("invalid");
                    int32 MatchingConflictResourceCount = 0;
                    FString MatchingConflictResourceSummary;
                    if (RuntimeCentralLocalConflictIndicesByLane.IsValidIndex(
                            FirstLaneIndex))
                    {
                        constexpr int32 MaximumLoggedConflictResources = 8;
                        for (const int32 ConflictIndex :
                             RuntimeCentralLocalConflictIndicesByLane[
                                 FirstLaneIndex])
                        {
                            if (!RuntimeCentralLocalConflicts.IsValidIndex(
                                    ConflictIndex))
                            {
                                continue;
                            }
                            const FRuntimeCentralLocalConflict& Conflict =
                                RuntimeCentralLocalConflicts[ConflictIndex];
                            const bool bForwardPair =
                                Conflict.FirstLaneIndex == FirstLaneIndex &&
                                Conflict.SecondLaneIndex == SecondLaneIndex;
                            const bool bReversePair =
                                Conflict.FirstLaneIndex == SecondLaneIndex &&
                                Conflict.SecondLaneIndex == FirstLaneIndex;
                            if (!bForwardPair && !bReversePair)
                            {
                                continue;
                            }
                            ++MatchingConflictResourceCount;
                            if (MatchingConflictResourceCount <=
                                MaximumLoggedConflictResources)
                            {
                                const float LoggedFirstBeginCm = bForwardPair
                                    ? Conflict.FirstBeginDistanceCm
                                    : Conflict.SecondBeginDistanceCm;
                                const float LoggedFirstEndCm = bForwardPair
                                    ? Conflict.FirstEndDistanceCm
                                    : Conflict.SecondEndDistanceCm;
                                const float LoggedSecondBeginCm = bForwardPair
                                    ? Conflict.SecondBeginDistanceCm
                                    : Conflict.FirstBeginDistanceCm;
                                const float LoggedSecondEndCm = bForwardPair
                                    ? Conflict.SecondEndDistanceCm
                                    : Conflict.FirstEndDistanceCm;
                                if (!MatchingConflictResourceSummary.IsEmpty())
                                {
                                    MatchingConflictResourceSummary += TEXT(",");
                                }
                                MatchingConflictResourceSummary += FString::Printf(
                                    TEXT("%d:%.1f-%.1f/%.1f-%.1f"),
                                    ConflictIndex,
                                    LoggedFirstBeginCm,
                                    LoggedFirstEndCm,
                                    LoggedSecondBeginCm,
                                    LoggedSecondEndCm);
                            }
                        }
                    }
                    if (bForceLog ||
                        bPeriodicHealthSample ||
                        CentralSevereOverlapPairObservationCount == 0)
                    {
                        UE_LOG(
                            LogTemp,
                            Warning,
                            TEXT("OPEN_MASS_CROWD_SEVERE_OVERLAP first_entity=%d second_entity=%d distance_cm=%.3f threshold_cm=%.2f first_lane=%d first_lane_id=%s first_progress=%.3f second_lane=%d second_lane_id=%s second_progress=%.3f matching_resources=%d resource_intervals=%s first_position=(%.3f,%.3f,%.3f) second_position=(%.3f,%.3f,%.3f)"),
                            FirstEntityIndex,
                            SecondEntityIndex,
                            FMath::Sqrt(SquaredDistance),
                            CentralSevereOverlapDistanceCm,
                            FirstLaneIndex,
                            *FirstLaneId,
                            FirstLaneDistance,
                            SecondLaneIndex,
                            *SecondLaneId,
                            SecondLaneDistance,
                            MatchingConflictResourceCount,
                            *MatchingConflictResourceSummary,
                            CertifiedPositions[FirstIndex].X,
                            CertifiedPositions[FirstIndex].Y,
                            CertifiedPositions[FirstIndex].Z,
                            CertifiedPositions[SecondIndex].X,
                            CertifiedPositions[SecondIndex].Y,
                            CertifiedPositions[SecondIndex].Z);
                    }
                }
            }
        }
        CentralMinimumEntityCenterDistanceCm = FMath::Sqrt(
            MinimumSquaredDistance);
        for (const uint8 bSeverelyOverlapped : SevereOverlapAgents)
        {
            CentralSevereOverlapAgentCount +=
                bSeverelyOverlapped != 0 ? 1 : 0;
        }
        CentralPeakSevereOverlapPairCount = FMath::Max(
            CentralPeakSevereOverlapPairCount,
            CentralSevereOverlapPairCount);
        CentralPeakSevereOverlapAgentCount = FMath::Max(
            CentralPeakSevereOverlapAgentCount,
            CentralSevereOverlapAgentCount);
        CentralSevereOverlapPairObservationCount =
            CentralSevereOverlapPairObservationCount >
                    MAX_int32 - CentralSevereOverlapPairCount
                ? MAX_int32
                : CentralSevereOverlapPairObservationCount +
                    CentralSevereOverlapPairCount;
        if (CentralMinimumObservedEntityCenterDistanceCm < 0.0f ||
            CentralMinimumEntityCenterDistanceCm <
                CentralMinimumObservedEntityCenterDistanceCm)
        {
            CentralMinimumObservedEntityCenterDistanceCm =
                CentralMinimumEntityCenterDistanceCm;
        }
    }

    CentralAdmittedEntityCount = SpawnedEntities.Num();
    CentralSimulatedEntityCount = SimulatedCount;
    CentralRepresentedEntityCount = RepresentedCount;

    if (bPeriodicHealthSample)
    {
        TArray<float> FrameTimes;
        FrameTimes.Reserve(
            CentralFrameTimeSamples.Num() -
            CentralFrameTimeFirstSampleIndex);
        for (int32 SampleIndex = CentralFrameTimeFirstSampleIndex;
             SampleIndex < CentralFrameTimeSamples.Num();
             ++SampleIndex)
        {
            FrameTimes.Add(
                CentralFrameTimeSamples[SampleIndex].FrameTimeMilliseconds);
        }
        Algo::Sort(FrameTimes);
        CentralFrameTimeSampleCount = FrameTimes.Num();
        if (!FrameTimes.IsEmpty())
        {
            const int32 P50Index = FMath::Clamp(
                FMath::CeilToInt(FrameTimes.Num() * 0.50f) - 1,
                0,
                FrameTimes.Num() - 1);
            const int32 P95Index = FMath::Clamp(
                FMath::CeilToInt(FrameTimes.Num() * 0.95f) - 1,
                0,
                FrameTimes.Num() - 1);
            CentralFrameTimeP50Ms = FrameTimes[P50Index];
            CentralFrameTimeP95Ms = FrameTimes[P95Index];
            CentralFrameTimeMaximumMs = FrameTimes.Last();
            CentralFrameTimeWindowSeconds = static_cast<float>(FMath::Max(
                0.0,
                GetWorld()->GetRealTimeSeconds() -
                    CentralFrameTimeSamples[
                        CentralFrameTimeFirstSampleIndex].WorldTimeSeconds));
        }
        else
        {
            CentralFrameTimeP50Ms = 0.0f;
            CentralFrameTimeP95Ms = 0.0f;
            CentralFrameTimeMaximumMs = 0.0f;
            CentralFrameTimeWindowSeconds = 0.0f;
        }
    }

    if (!bForceLog && !bPeriodicHealthSample)
    {
        return;
    }

    LastLoggedCentralRepresentedCount = RepresentedCount;
    FString DistrictTelemetry;
    TArray<TMap<FName, int32>> SpawnComponentCountsByDistrict;
    SpawnComponentCountsByDistrict.SetNum(RuntimeCentralDistricts.Num());
    const int32 AdmittedPlanCount = FMath::Min(
        SpawnedEntities.Num(),
        FMath::Min(
            CentralSpawnDistrictPlan.Num(),
            CentralSpawnLanePlan.Num()));
    for (int32 EntityIndex = 0;
         EntityIndex < AdmittedPlanCount;
         ++EntityIndex)
    {
        const int32 DistrictIndex = CentralSpawnDistrictPlan[EntityIndex];
        const int32 LaneIndex = CentralSpawnLanePlan[EntityIndex];
        if (SpawnComponentCountsByDistrict.IsValidIndex(DistrictIndex) &&
            RuntimeCentralLaneComponentIds.IsValidIndex(LaneIndex))
        {
            ++SpawnComponentCountsByDistrict[DistrictIndex].FindOrAdd(
                RuntimeCentralLaneComponentIds[LaneIndex]);
        }
    }
    FString SpawnComponentTelemetry;
    for (int32 DistrictIndex = 0;
         DistrictIndex < RuntimeCentralDistricts.Num();
         ++DistrictIndex)
    {
        if (!DistrictTelemetry.IsEmpty())
        {
            DistrictTelemetry += TEXT(",");
        }
        DistrictTelemetry += FString::Printf(
            TEXT("%s:%d"),
            *RuntimeCentralDistricts[DistrictIndex].DistrictId.ToString(),
            RuntimeCentralDistricts[DistrictIndex].AdmittedPopulation);
        if (!SpawnComponentTelemetry.IsEmpty())
        {
            SpawnComponentTelemetry += TEXT("|");
        }
        FString CellIds;
        for (const FName CellId :
             RuntimeCentralDistricts[DistrictIndex].CellIds)
        {
            if (!CellIds.IsEmpty())
            {
                CellIds += TEXT("+");
            }
            CellIds += CellId.ToString();
        }
        TArray<FName> ComponentIds;
        SpawnComponentCountsByDistrict[DistrictIndex].GetKeys(ComponentIds);
        ComponentIds.Sort(
            [](const FName A, const FName B)
            {
                return A.LexicalLess(B);
            });
        FString ComponentCounts;
        for (const FName ComponentId : ComponentIds)
        {
            if (!ComponentCounts.IsEmpty())
            {
                ComponentCounts += TEXT("+");
            }
            ComponentCounts += FString::Printf(
                TEXT("%s:%d"),
                *ComponentId.ToString(),
                SpawnComponentCountsByDistrict[DistrictIndex].FindChecked(
                    ComponentId));
        }
        SpawnComponentTelemetry += FString::Printf(
            TEXT("%s@%s{%s}"),
            *RuntimeCentralDistricts[DistrictIndex].DistrictId.ToString(),
            *CellIds,
            *ComponentCounts);
    }
    UE_LOG(
        LogTemp,
        Warning,
        TEXT("OPEN_MASS_CROWD_CENTRAL_TELEMETRY simulated=%d represented=%d admitted=%d target=%d batch=%d complete=%s short_path_chunks=%d moving=%d expected_moving=%d stuck_gt5s=%d overlap_threshold_cm=%.2f overlap_pairs=%d overlap_agents=%d overlap_peak_pairs=%d overlap_pair_observations=%d invalid_position_observations=%d min_center_cm=%.2f min_observed_center_cm=%.2f local_conflict_resources=%d local_conflict_clusters=%d strict_local_pairs=%d uncovered_local_pairs=%d admission_scans=%d admission_violations=%d node_reservation_holds=%d corridor_direction_holds=%d local_conflict_holds=%d conflict_wait_replans=%d maximum_conflict_wait_s=%.3f high_actor=%d low_actor=%d vat_ism=%d frame_p50_ms=%.2f frame_p95_ms=%.2f frame_max_ms=%.2f frame_samples=%d frame_window_s=%.2f ground_queries=%d ground_component_tests=%d ground_cache_refreshes=%d unsupported=%d districts=%s district_cell_spawn_components=%s stuck_entities=%s"),
        CentralSimulatedEntityCount,
        CentralRepresentedEntityCount,
        CentralAdmittedEntityCount,
        CentralAdmissionTargetCount,
        CentralAdmissionBatchCount,
        CentralAdmittedEntityCount == CentralAdmissionTargetCount
            ? TEXT("true")
            : TEXT("false"),
        CentralShortPathChunkCount,
        CentralMovingEntityCount,
        CentralExpectedMovingEntityCount,
        CentralStuckEntityCount,
        CentralSevereOverlapDistanceCm,
        CentralSevereOverlapPairCount,
        CentralSevereOverlapAgentCount,
        CentralPeakSevereOverlapPairCount,
        CentralSevereOverlapPairObservationCount,
        CentralInvalidPositionObservationCount,
        CentralMinimumEntityCenterDistanceCm,
        CentralMinimumObservedEntityCenterDistanceCm,
        RuntimeCentralLocalConflicts.Num(),
        RuntimeCentralLocalConflictClusterCount,
        CentralStrictLocalConflictPairCount,
        CentralUncoveredLocalConflictPairCount,
        CentralAdmissionClearanceScanCount,
        CentralAdmissionClearanceViolationCount,
        CentralNodeReservationHoldCount,
        CentralCorridorDirectionHoldCount,
        CentralLocalConflictHoldCount,
        CentralConflictWaitReplanCount,
        CentralMaximumConflictWaitSeconds,
        CentralHighActorRepresentationCount,
        CentralLowActorRepresentationCount,
        CentralVATRepresentationCount,
        CentralFrameTimeP50Ms,
        CentralFrameTimeP95Ms,
        CentralFrameTimeMaximumMs,
        CentralFrameTimeSampleCount,
        CentralFrameTimeWindowSeconds,
        CentralGroundGuardQueryCount,
        CentralGroundCandidateComponentTestCount,
        CentralGroundComponentCacheRefreshCount,
        CurrentUnsupportedVisualCount,
        *DistrictTelemetry,
        *SpawnComponentTelemetry,
        StuckEntityTelemetry.IsEmpty()
            ? TEXT("none")
            : *StuckEntityTelemetry);
}

void AOpenMassCrowdSpawner::ResetCentralSessionTelemetry()
{
    CentralPeakSevereOverlapPairCount = 0;
    CentralPeakSevereOverlapAgentCount = 0;
    CentralSevereOverlapPairObservationCount = 0;
    CentralInvalidPositionObservationCount = 0;
    CentralAdmissionClearanceScanCount = 0;
    CentralAdmissionClearanceViolationCount = 0;
    CentralNodeReservationHoldCount = 0;
    CentralCorridorDirectionHoldCount = 0;
    CentralLocalConflictHoldCount = 0;
    CentralConflictWaitReplanCount = 0;
    CentralStallRecoveryReplanCount = 0;
    CentralInvestorCachedGroundFallbackCount = 0;
    CentralEdgeLivenessAdvanceCount = 0;
    CentralMaximumConflictWaitSeconds = 0.0f;
    CentralTelemetryObservationCount = 0;
    CentralMinimumObservedEntityCenterDistanceCm = -1.0f;
    ResetCentralRuntimeTelemetry();
}

void AOpenMassCrowdSpawner::ResetCentralRuntimeTelemetry()
{
    CentralExpectedMovingEntityCount = 0;
    CentralMovingEntityCount = 0;
    CentralStuckEntityCount = 0;
    CentralSevereOverlapPairCount = 0;
    CentralSevereOverlapAgentCount = 0;
    CentralHighActorRepresentationCount = 0;
    CentralLowActorRepresentationCount = 0;
    CentralVATRepresentationCount = 0;
    CentralFrameTimeSampleCount = 0;
    CentralMinimumEntityCenterDistanceCm = -1.0f;
    CentralFrameTimeP50Ms = 0.0f;
    CentralFrameTimeP95Ms = 0.0f;
    CentralFrameTimeMaximumMs = 0.0f;
    CentralFrameTimeWindowSeconds = 0.0f;
    CentralTelemetrySampleAccumulator = 0.0f;
    CentralTelemetryLastCertifiedPositions.Reset();
    CentralTelemetryLastCertifiedPositions.SetNumZeroed(
        CentralAdmissionTargetCount);
    CentralTelemetryStationarySeconds.Reset();
    CentralTelemetryStationarySeconds.SetNumZeroed(
        CentralAdmissionTargetCount);
    CentralTelemetryPositionValid.Reset();
    CentralTelemetryPositionValid.SetNumZeroed(
        CentralAdmissionTargetCount);
    RuntimeCentralCorridorWaitSeconds.Reset();
    RuntimeCentralCorridorWaitSeconds.SetNumZeroed(
        CentralAdmissionTargetCount);
    RuntimeCentralCorridorWaitLaneIndices.Init(
        INDEX_NONE,
        CentralAdmissionTargetCount);
    RuntimeCentralLocalConflictWaitSeconds.Reset();
    RuntimeCentralLocalConflictWaitSeconds.SetNumZeroed(
        CentralAdmissionTargetCount);
    RuntimeCentralLocalConflictWaitResourceIndices.Init(
        INDEX_NONE,
        CentralAdmissionTargetCount);
    RuntimeCentralPreviousFrameStates.Reset();
    RuntimeCentralPreviousFrameStates.SetNum(
        CentralAdmissionTargetCount);
    RuntimeCentralPreviousFrameShortPaths.Reset();
    RuntimeCentralPreviousFrameShortPaths.SetNum(
        CentralAdmissionTargetCount);
    RuntimeCentralPreviousFrameMoveTargets.Reset();
    RuntimeCentralPreviousFrameMoveTargets.SetNum(
        CentralAdmissionTargetCount);
    RuntimeCentralPreviousFrameNavigationValid.SetNumZeroed(
        CentralAdmissionTargetCount);
    RuntimeCentralYieldAnchorStates.Reset();
    RuntimeCentralYieldAnchorStates.SetNum(
        CentralAdmissionTargetCount);
    RuntimeCentralYieldAnchorShortPaths.Reset();
    RuntimeCentralYieldAnchorShortPaths.SetNum(
        CentralAdmissionTargetCount);
    RuntimeCentralYieldAnchorMoveTargets.Reset();
    RuntimeCentralYieldAnchorMoveTargets.SetNum(
        CentralAdmissionTargetCount);
    RuntimeCentralYieldAnchorNavigationValid.SetNumZeroed(
        CentralAdmissionTargetCount);
    CentralFrameTimeSamples.Reset();
    CentralFrameTimeFirstSampleIndex = 0;
}

void AOpenMassCrowdSpawner::RefreshCompletedPaths()
{
    if (NetworkMode == EOpenMassCrowdNetworkMode::CentralCertifiedCache &&
        !bCentralAdmissionReleased)
    {
        return;
    }

    UMassSpawnerSubsystem* SpawnerSubsystem =
        UWorld::GetSubsystem<UMassSpawnerSubsystem>(GetWorld());
    if (!SpawnerSubsystem)
    {
        return;
    }

    FMassEntityManager& EntityManager = SpawnerSubsystem->GetEntityManagerChecked();
    for (int32 EntityIndex = 0; EntityIndex < SpawnedEntities.Num(); ++EntityIndex)
    {
        if (NetworkMode == EOpenMassCrowdNetworkMode::CentralCertifiedCache &&
            EntityRouteStates.IsValidIndex(EntityIndex) &&
            EntityRouteStates[EntityIndex].bWaitingForAvailableCell)
        {
            continue;
        }
        const FMassEntityHandle Entity = SpawnedEntities[EntityIndex];
        if (!EntityManager.IsEntityValid(Entity))
        {
            continue;
        }

        const FMassZoneGraphShortPathFragment& ShortPath =
            EntityManager.GetFragmentDataChecked<FMassZoneGraphShortPathFragment>(Entity);
        bool bShouldRefreshPath = ShortPath.IsDone();
        if (!bShouldRefreshPath && bUseCentralCertifiedEdgeCirculation &&
            EntityRouteStates.IsValidIndex(EntityIndex))
        {
            const FEntityRouteState& RouteState =
                EntityRouteStates[EntityIndex];
            const FMassZoneGraphLaneLocationFragment& LaneLocation =
                EntityManager.GetFragmentDataChecked<
                    FMassZoneGraphLaneLocationFragment>(Entity);
            const bool bOnFinalRouteLane =
                RouteState.CurrentPathIndex == RouteState.LanePath.Num() - 1 &&
                RouteState.LanePath.IsValidIndex(
                    RouteState.CurrentPathIndex) &&
                RouteState.LanePath[RouteState.CurrentPathIndex] ==
                    LaneLocation.LaneHandle;
            // Queue the reverse circulation while the current action still
            // has a few centimetres of motion. Waiting for IsDone() alone can
            // leave a short UE Mass path at Stand for one or more telemetry
            // samples before its next action is observed.
            bShouldRefreshPath = bOnFinalRouteLane &&
                LaneLocation.DistanceAlongLane >=
                    RouteState.DestinationDistance - 5.0f;
        }
        if (bShouldRefreshPath && !RequestNextPath(EntityIndex))
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
    // Every Central admission slot has already passed an exact-XY live Cesium
    // probe in the committing transaction. Until all batches exist, keep those
    // entities still and do not let a second transient streaming query mark a
    // cell unavailable. The normal rolling live guard starts immediately after
    // the complete population is released.
    if (NetworkMode == EOpenMassCrowdNetworkMode::CentralCertifiedCache &&
        !bCentralAdmissionReleased)
    {
        return;
    }

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
    const bool bCentralMode =
        NetworkMode == EOpenMassCrowdNetworkMode::CentralCertifiedCache;
    const int32 GroundGuardsPerPass =
        bCentralMode && bInvestorDeliveryDemoEnabled
        ? InvestorGroundGuardsPerPass
        : CentralGroundGuardsPerTick;
    const int32 GroundGuardBucketCount = bCentralMode
        ? FMath::Max(
            1,
            FMath::DivideAndRoundUp(
                SpawnedEntities.Num(),
                GroundGuardsPerPass))
        : 1;
    const int32 GroundGuardBucket = bCentralMode
        ? CentralGroundGuardBucketCursor % GroundGuardBucketCount
        : 0;
    if (bCentralMode)
    {
        // This is the only all-component operation in the runtime guard path,
        // and its one-second refresh is shared by every staggered entity probe.
        RefreshCesiumGroundComponentCache();
    }

    int32 GuardedCount = 0;
    int32 CorrectedCount = 0;
    TArray<int32> PathsToRebuild;
    for (int32 EntityIndex = 0; EntityIndex < SpawnedEntities.Num(); ++EntityIndex)
    {
        if (bCentralMode &&
            EntityIndex % GroundGuardBucketCount != GroundGuardBucket)
        {
            continue;
        }
        const FMassEntityHandle Entity = SpawnedEntities[EntityIndex];
        if (!EntityManager.IsEntityValid(Entity))
        {
            continue;
        }
        ++GuardedCount;

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
        const int32 RuntimeLaneIndex = LaneLocation.LaneHandle.Index;
        const FName CentralCellId =
            bCentralMode && RuntimeCentralLaneCellIds.IsValidIndex(RuntimeLaneIndex)
                ? RuntimeCentralLaneCellIds[RuntimeLaneIndex]
                : NAME_None;

        // A failed cell is not reopened merely because a different point in
        // the same 150 m cell remains cached. Re-probe the exact certified lane
        // point that failed before allowing this entity to contribute to the
        // cell's recovery quorum. The pedestrian remains at LastValid while the
        // recovery probe runs.
        if (bCentralMode && LastValid && LastValid->bHasRecoveryProbe)
        {
            // Investor delivery uses an offline exact-XY certified ground
            // cache as the authoritative walk surface. A live Cesium trace
            // can miss transiently while a tile collision payload streams,
            // even though the already-certified lane remains visible and
            // valid. Do not convert that streaming miss into a permanent
            // Stand action or close the whole 150 m route cell.
            if (bInvestorDeliveryDemoEnabled)
            {
                LastValid->bHasRecoveryProbe = false;
                LastValid->ConsecutiveMisses = 0;
                LastValid->bUnsupported = false;
                RecordCentralCellGroundGuard(
                    CentralCellId,
                    EntityIndex,
                    true);
                if (EntityRouteStates.IsValidIndex(EntityIndex) &&
                    EntityRouteStates[EntityIndex].bWaitingForAvailableCell)
                {
                    PathsToRebuild.Add(EntityIndex);
                }
                ++CentralInvestorCachedGroundFallbackCount;
                ++CorrectedCount;
                continue;
            }

            ++CentralGroundGuardQueryCount;
            FVector RecoveryGroundPoint;
            if (ProjectToCesiumGround(
                    LastValid->RecoveryProbePoint,
                    RecoveryGroundPoint))
            {
                LastValid->bHasRecoveryProbe = false;
                LastValid->ConsecutiveMisses = 0;
                LastValid->bUnsupported = false;
                RecordCentralCellGroundGuard(
                    CentralCellId,
                    EntityIndex,
                    true);
                ++CorrectedCount;
                continue;
            }

            ++GroundProjectionFailureCount;
            ++LastValid->ConsecutiveMisses;
            MaxConsecutiveGroundMisses = FMath::Max(
                MaxConsecutiveGroundMisses,
                LastValid->ConsecutiveMisses);
            RecordCentralCellGroundGuard(
                CentralCellId,
                EntityIndex,
                false);
            if (HoldCentralEntityAtCertifiedPosition(
                    EntityIndex,
                    TEXT("ground_guard_recovery_probe_failed")))
            {
                ++GroundRollbackCount;
                continue;
            }

            LastValid->bUnsupported = true;
            ++GroundUnrecoverableCount;
            continue;
        }

        const FVector CurrentLocation = Transform.GetLocation();
        FVector GuardQueryLocation = CurrentLocation;
        bool bTestingPresentationOffset = false;
        if (bCentralMode && bInvestorDeliveryDemoEnabled &&
            CentralPresentationOffsetValid.IsValidIndex(EntityIndex) &&
            !FMath::IsNearlyZero(
                GetInvestorPresentationOffsetCm(EntityIndex)))
        {
            FZoneGraphLaneLocation PresentationLocation;
            if (ZoneGraphSubsystem->CalculateLocationAlongLane(
                    LaneLocation.LaneHandle,
                    FMath::Clamp(
                        LaneLocation.DistanceAlongLane +
                            GetInvestorPresentationPhaseOffsetCm(EntityIndex),
                        0.0f,
                        LaneLocation.LaneLength),
                    PresentationLocation))
            {
                GuardQueryLocation = GetInvestorPresentationPosition(
                    PresentationLocation,
                    EntityIndex);
                bTestingPresentationOffset = true;
            }
        }
        FVector GroundPoint;
        if (bCentralMode)
        {
            ++CentralGroundGuardQueryCount;
        }
        if (ProjectToCesiumGround(GuardQueryLocation, GroundPoint))
        {
            const FVector ValidLocation =
                bCentralMode
                ? (bTestingPresentationOffset
                    ? GroundPoint + FVector(0.0, 0.0, LaneHeightOffset)
                    : CurrentLocation)
                : GroundPoint + FVector(0.0, 0.0, LaneHeightOffset);
            // Central's cached 10 cm polyline is already the strict
            // collision-certified ground transform. The live exact-XY query
            // above is a streaming/support guard only. Replacing the certified
            // Z with a later raw hit changed pair distance after the certified
            // transform processor had proved the 20 cm gate, producing a
            // repeatable 19.97 cm rendered overlap on cross-sloped tracks.
            Transform.SetLocation(ValidLocation);
            if (bCentralMode &&
                CentralPresentationOffsetValid.IsValidIndex(EntityIndex))
            {
                CentralPresentationOffsetValid[EntityIndex] = 1;
            }
            if (LastValid)
            {
                LastValid->Transform = Transform;
                LastValid->LaneHandle = LaneLocation.LaneHandle;
                LastValid->DistanceAlongLane = LaneLocation.DistanceAlongLane;
                LastValid->LaneLength = LaneLocation.LaneLength;
                LastValid->ConsecutiveMisses = 0;
                LastValid->bUnsupported = false;
                LastValid->bValid = true;
            }
            if (bCentralMode)
            {
                RecordCentralCellGroundGuard(
                    CentralCellId,
                    EntityIndex,
                    true);
            }
            ++CorrectedCount;
            continue;
        }

        ++GroundProjectionFailureCount;
        if (bCentralMode &&
            CentralPresentationOffsetValid.IsValidIndex(EntityIndex))
        {
            // An unsupported band is presentation-only. Remove it before the
            // existing certified-center recovery path runs below.
            CentralPresentationOffsetValid[EntityIndex] = 0;
        }
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
        const bool bCalculatedCenter =
            ZoneGraphSubsystem->CalculateLocationAlongLane(
                LaneLocation.LaneHandle,
                LaneLocation.DistanceAlongLane,
                CenterLaneLocation);
        if (bCentralMode && bCalculatedCenter)
        {
            ++CentralGroundGuardQueryCount;
        }
        const bool bCenterSupported = bCalculatedCenter &&
            ProjectToCesiumGround(CenterLaneLocation.Position, CenterGroundPoint);
        if (bCenterSupported)
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
                LastValid->bUnsupported = false;
                LastValid->bValid = true;
            }
            if (bCentralMode)
            {
                RecordCentralCellGroundGuard(
                    CentralCellId,
                    EntityIndex,
                    true);
            }
            ++GroundCenterRecoveryCount;
            ++CorrectedCount;
            if (!bCentralMode || IsCentralRuntimeLaneAvailable(RuntimeLaneIndex))
            {
                PathsToRebuild.Add(EntityIndex);
            }
            continue;
        }

        if (bCentralMode && bInvestorDeliveryDemoEnabled && LastValid)
        {
            // The runtime transform was clamped earlier in this tick to the
            // complete collision-certified lane cache. Preserve that exact
            // cached sample when both optional live probes miss; retry the
            // rolling monitor later without stopping or replanning the agent.
            LastValid->Transform = Transform;
            LastValid->LaneHandle = LaneLocation.LaneHandle;
            LastValid->DistanceAlongLane = LaneLocation.DistanceAlongLane;
            LastValid->LaneLength = LaneLocation.LaneLength;
            LastValid->ConsecutiveMisses = 0;
            LastValid->bHasRecoveryProbe = false;
            LastValid->bUnsupported = false;
            LastValid->bValid = true;
            RecordCentralCellGroundGuard(
                CentralCellId,
                EntityIndex,
                true);
            ++CentralInvestorCachedGroundFallbackCount;
            ++CorrectedCount;
            continue;
        }

        if (bCentralMode)
        {
            // A failed exact-XY probe and a failed certified lane-center probe
            // mean the current streamed cell is no longer trustworthy. Fail
            // the entire cell closed, restore the entity's last certified Mass
            // and lane state, and let the availability revision replan every
            // affected route on the following tick.
            if (LastValid)
            {
                LastValid->RecoveryProbePoint = bCalculatedCenter
                    ? CenterLaneLocation.Position
                    : CurrentLocation;
                LastValid->bHasRecoveryProbe = true;
            }
            RecordCentralCellGroundGuard(
                CentralCellId,
                EntityIndex,
                false);
            if (LastValid && LastValid->bValid &&
                HoldCentralEntityAtCertifiedPosition(
                    EntityIndex,
                    TEXT("ground_guard_cell_unavailable")))
            {
                ++GroundRollbackCount;
                continue;
            }

            if (LastValid)
            {
                LastValid->bUnsupported = true;
            }
            ++GroundUnrecoverableCount;
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
            LastValid->bUnsupported = false;
            ++GroundRollbackCount;
            PathsToRebuild.Add(EntityIndex);
            continue;
        }

        if (LastValid)
        {
            LastValid->bUnsupported = true;
        }
        ++GroundUnrecoverableCount;
    }

    if (bCentralMode)
    {
        CentralGroundGuardBucketCursor =
            (GroundGuardBucket + 1) % GroundGuardBucketCount;
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

    CurrentUnsupportedVisualCount = 0;
    for (const FLastValidGroundState& GroundState : LastValidGroundStates)
    {
        CurrentUnsupportedVisualCount += GroundState.bUnsupported ? 1 : 0;
    }

    if (CorrectedCount != GuardedCount)
    {
        UE_LOG(
            LogTemp,
            Verbose,
            TEXT("OPEN_MASS_CROWD_GROUND corrected=%d guarded=%d total=%d bucket=%d/%d unavailable_cells=%d"),
            CorrectedCount,
            GuardedCount,
            SpawnedEntities.Num(),
            GroundGuardBucket + 1,
            GroundGuardBucketCount,
            RuntimeUnavailableCentralCellIds.Num());
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
        if (NetworkMode == EOpenMassCrowdNetworkMode::CentralCertifiedCache &&
            LastValid && LastValid->bValid)
        {
            // ConstrainCentralTransformsToCertifiedLanes already placed the
            // Mass transform on the complete 10 cm offline-certified polyline.
            // A live-query failure restores that same fragment to LastValid.
            // Consuming it directly keeps actors smooth and makes them match
            // the transform consumed by VAT/ISM on the following Mass frame.
            VisualActor->SetActorHiddenInGame(false);
        }
        else if (LastValid && LastValid->bValid)
        {
            // Preserve the proven local fallback's live exact-XY visual lock.
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

void AOpenMassCrowdSpawner::EnsureInvestorDemoInitialized()
{
    if (bInvestorDemoInitialized || SpawnedEntities.IsEmpty() ||
        !bCentralAdmissionReleased)
    {
        return;
    }

    RegisterInvestorLegacySignalGuards();

    InvestorStations.Reset();
    FInvestorStationRuntime& Hero = InvestorStations.AddDefaulted_GetRef();
    Hero.StationId = TEXT("CENTRAL-ROOF-01");
    Hero.ConfiguredRoofPoint = FVector(-160000.0, 247000.0, 12735.574);
    Hero.CoverageRadiusCm = 23000.0f;
    Hero.DisplayColor = FColor(25, 225, 245);

    FInvestorStationRuntime& Support = InvestorStations.AddDefaulted_GetRef();
    Support.StationId = TEXT("CENTRAL-WEST-ROOF-02");
    Support.ConfiguredRoofPoint = FVector(-169000.0, 247000.0, 12382.011);
    Support.CoverageRadiusCm = 20500.0f;
    Support.DisplayColor = FColor(255, 174, 42);

    InvestorPeople.Reset();
    InvestorPeople.SetNum(SpawnedEntities.Num());
    for (int32 StableIndex = 0; StableIndex < InvestorPeople.Num(); ++StableIndex)
    {
        InvestorPeople[StableIndex].CurrentApplicationIndex =
            (StableIndex * 7 + 3) % 8;
    }
    if (!CentralSpawnPositionPlan.IsEmpty())
    {
        InvestorBuildingPortalLocation = CentralSpawnPositionPlan[0];
    }

    // Remove every already-loaded actor from the obsolete mock channel layer
    // before exposing the collision-certified scene. World Partition can load
    // more of those actors later, so UpdateInvestorDemo repeats this bounded
    // suppression while PIE is active.
    SuppressLegacyFloatingSignalActors();

    // The persisted SIG_Source_/SIG_Ray_ actors are the completed rooftop
    // channel work and remain the single visual source in both the editor and
    // PIE worlds. Starting the crowd must not hide, move, rebuild, instance, or
    // otherwise take ownership of those actors.
    ValidateInvestorPersistedSignalLayer();
    ClearInvestorAssociationVisuals();
    if (IsValid(InvestorAssociationLineBatch))
    {
        InvestorAssociationLineBatch->SetVisibility(true, true);
    }
    ShowInvestorKPI();
    InvestorLegacySignalSuppressionAccumulator = 0.0f;
    InvestorNetworkUpdateAccumulator = 0.0f;
    InvestorRoofValidationAccumulator = 0.0f;
    InvestorAssociationVisualRefreshAccumulator = 0.0f;
    InvestorProfileRefreshAccumulator = 0.0f;
    InvestorElapsedSeconds = 0.0f;
    InvestorBuildingEntryCount = 0;
    InvestorBuildingExitCount = 0;
    InvestorStationReacquisitionCount = 0;
    bInvestorAutoProfileOpened = false;
    bInvestorDemoInitialized = true;

    UE_LOG(
        LogTemp,
        Warning,
        TEXT("INVESTOR_TELECOM_DEMO_INITIALIZED people=%d stations=%d portal=(%.1f,%.1f,%.1f) persisted_signal_policy=unchanged_editor_actors runtime_signal_overlay=false"),
        InvestorPeople.Num(),
        InvestorStations.Num(),
        InvestorBuildingPortalLocation.X,
        InvestorBuildingPortalLocation.Y,
        InvestorBuildingPortalLocation.Z);
}

bool AOpenMassCrowdSpawner::ValidateInvestorStationRoof(
    FInvestorStationRuntime& Station)
{
    ++Station.ValidationAttempts;
    TArray<UPrimitiveComponent*> Components;
    GatherSpatiallyRelevantCesiumComponents(
        Station.ConfiguredRoofPoint,
        Components);
    const auto RecordMiss = [&Station]()
    {
        ++Station.ConsecutiveValidationMisses;
        if (Station.ConsecutiveValidationMisses >= 4)
        {
            Station.bRoofValidated = false;
        }
        return false;
    };
    if (Components.IsEmpty())
    {
        return RecordMiss();
    }

    const FVector Start =
        Station.ConfiguredRoofPoint + FVector(0.0, 0.0, 5000.0);
    const FVector End =
        Station.ConfiguredRoofPoint - FVector(0.0, 0.0, 5000.0);
    FCollisionQueryParams Params(
        SCENE_QUERY_STAT(InvestorTelecomRoof),
        true,
        this);
    Params.AddIgnoredActor(this);
    float HighestRoofZ = -TNumericLimits<float>::Max();
    FHitResult BestHit;
    bool bFound = false;
    for (UPrimitiveComponent* Component : Components)
    {
        if (!IsCesiumQueryComponent(Component, GetWorld()))
        {
            continue;
        }
        FHitResult Hit;
        if (Component->LineTraceComponent(Hit, Start, End, Params) &&
            Hit.ImpactNormal.Z >= 0.7f &&
            Hit.ImpactPoint.Z >= 5000.0f)
        {
            // Cesium replaces photogrammetry tiles as LOD changes. The stored
            // Z is only a search hint; using the highest current walkable hit
            // at the configured XY keeps the node on the real visible roof.
            if (Hit.ImpactPoint.Z > HighestRoofZ)
            {
                HighestRoofZ = Hit.ImpactPoint.Z;
                BestHit = Hit;
                bFound = true;
            }
        }
    }

    if (!bFound)
    {
        return RecordMiss();
    }

    const bool bWasValidated = Station.bRoofValidated;
    const float PreviousRoofZ = Station.ValidatedRoofPoint.Z;
    Station.ValidatedRoofPoint = BestHit.ImpactPoint;
    constexpr float PresentationBaseOffsetCm = 4.0f;
    Station.RoofErrorCm = PresentationBaseOffsetCm;
    Station.ConfiguredAnchorAdjustmentCm = FMath::Abs(
        BestHit.ImpactPoint.Z - Station.ConfiguredRoofPoint.Z);
    Station.ConsecutiveValidationMisses = 0;
    Station.bRoofValidated = true;
    if (!bWasValidated ||
        FMath::Abs(PreviousRoofZ - Station.ValidatedRoofPoint.Z) > 50.0f)
    {
        UE_LOG(
            LogTemp,
            Warning,
            TEXT("INVESTOR_TELECOM_ROOF_VALIDATED station=%s attempts=%d mount_offset_cm=%.2f lod_adjustment_cm=%.2f point=(%.1f,%.1f,%.1f) component=%s live_revalidation=true"),
            *Station.StationId.ToString(),
            Station.ValidationAttempts,
            Station.RoofErrorCm,
            Station.ConfiguredAnchorAdjustmentCm,
            Station.ValidatedRoofPoint.X,
            Station.ValidatedRoofPoint.Y,
            Station.ValidatedRoofPoint.Z,
            *GetNameSafe(BestHit.GetComponent()));
    }
    return true;
}

void AOpenMassCrowdSpawner::UpdateInvestorDemo(const float DeltaSeconds)
{
    if (!bInvestorDemoInitialized || !GetWorld())
    {
        return;
    }

    InvestorElapsedSeconds += FMath::Max(DeltaSeconds, 0.0f);
    InvestorLegacySignalSuppressionAccumulator += DeltaSeconds;
    if (InvestorLegacySignalSuppressionAccumulator >=
        InvestorLegacySignalSuppressionSeconds)
    {
        InvestorLegacySignalSuppressionAccumulator = FMath::Fmod(
            InvestorLegacySignalSuppressionAccumulator,
            InvestorLegacySignalSuppressionSeconds);
        SuppressLegacyFloatingSignalActors();
    }
    InvestorRoofValidationAccumulator += DeltaSeconds;
    const float RoofRefreshSeconds =
        GetInvestorValidatedStationCount() == InvestorStations.Num()
        ? InvestorValidatedRoofRefreshSeconds
        : 0.5f;
    if (InvestorRoofValidationAccumulator >= RoofRefreshSeconds)
    {
        InvestorRoofValidationAccumulator = 0.0f;
        for (FInvestorStationRuntime& Station : InvestorStations)
        {
            ValidateInvestorStationRoof(Station);
        }
    }

    InvestorNetworkUpdateAccumulator += DeltaSeconds;
    if (InvestorNetworkUpdateAccumulator >= InvestorNetworkRefreshSeconds)
    {
        const float StepSeconds = InvestorNetworkUpdateAccumulator;
        InvestorNetworkUpdateAccumulator = 0.0f;
        UpdateInvestorPersonStates(StepSeconds);
    }

    InvestorProfileRefreshAccumulator += DeltaSeconds;
    if (!bInvestorAutoProfileOpened && InvestorElapsedSeconds >= 3.0f &&
        SpawnedEntities.IsValidIndex(0))
    {
        bInvestorAutoProfileOpened = ShowCentralProfileByStableIndex(0);
        InvestorProfileRefreshAccumulator = 0.0f;
    }
    else if (SelectedCentralProfileEntityIndex != INDEX_NONE &&
        InvestorProfileRefreshAccumulator >= 0.75f)
    {
        const int32 StableIndex = SelectedCentralProfileEntityIndex;
        InvestorProfileRefreshAccumulator = 0.0f;
        ShowCentralProfileByStableIndex(StableIndex);
    }

    // This is a separate person-association layer, not another propagation
    // system. The persisted rooftop channel actors remain the single signal
    // source of truth; these transient links only show which live person is
    // served by which collision-validated rooftop point.
    InvestorAssociationVisualRefreshAccumulator += DeltaSeconds;
    if (InvestorAssociationVisualRefreshAccumulator >=
        InvestorAssociationVisualRefreshSeconds)
    {
        InvestorAssociationVisualRefreshAccumulator = FMath::Fmod(
            InvestorAssociationVisualRefreshAccumulator,
            InvestorAssociationVisualRefreshSeconds);
        DrawInvestorAssociationVisuals();
    }
}

void AOpenMassCrowdSpawner::UpdateInvestorPersonStates(const float DeltaSeconds)
{
    UMassSpawnerSubsystem* SpawnerSubsystem =
        UWorld::GetSubsystem<UMassSpawnerSubsystem>(GetWorld());
    if (!SpawnerSubsystem || InvestorPeople.Num() != SpawnedEntities.Num())
    {
        return;
    }
    FMassEntityManager& EntityManager =
        SpawnerSubsystem->GetEntityManagerChecked();

    for (int32 StableIndex = 0;
         StableIndex < InvestorPeople.Num();
         ++StableIndex)
    {
        const FMassEntityHandle Entity = SpawnedEntities[StableIndex];
        if (!EntityManager.IsEntityValid(Entity))
        {
            continue;
        }
        FTransformFragment* Transform =
            EntityManager.GetFragmentDataPtr<FTransformFragment>(Entity);
        if (!Transform)
        {
            continue;
        }

        FInvestorPersonRuntime& Person = InvestorPeople[StableIndex];
        const FVector PersonLocation = Transform->GetTransform().GetLocation();
        Person.EntryCooldownRemainingSeconds = FMath::Max(
            0.0f,
            Person.EntryCooldownRemainingSeconds - DeltaSeconds);

        // Stable person zero is the repeatable building-boundary story target.
        // Its portal is its certified route spawn, so entry never fabricates a
        // roof/bridge path and remains on the proven ground network.
        if (StableIndex == 0 &&
            Person.LocationState == EInvestorPersonLocationState::Outdoor &&
            InvestorElapsedSeconds >= 6.0f &&
            Person.EntryCooldownRemainingSeconds <= 0.0f &&
            FVector::DistSquared2D(
                PersonLocation,
                InvestorBuildingPortalLocation) <= FMath::Square(1500.0f))
        {
            Person.LocationState = EInvestorPersonLocationState::Entering;
            Person.TransitionRemainingSeconds = 1.25f;
            Person.ServingStationIndex = INDEX_NONE;
            Person.SignalQualityPercent = 0.0f;
            ++InvestorBuildingEntryCount;
            UE_LOG(
                LogTemp,
                Warning,
                TEXT("INVESTOR_TELECOM_BUILDING_ENTER person=%s portal=(%.1f,%.1f,%.1f)"),
                *GetCentralPersonId(StableIndex),
                InvestorBuildingPortalLocation.X,
                InvestorBuildingPortalLocation.Y,
                InvestorBuildingPortalLocation.Z);
        }

        switch (Person.LocationState)
        {
        case EInvestorPersonLocationState::Entering:
            Person.TransitionRemainingSeconds -= DeltaSeconds;
            Person.VisibilityAlpha = FMath::Clamp(
                Person.TransitionRemainingSeconds / 1.25f,
                0.0f,
                1.0f);
            if (Person.TransitionRemainingSeconds <= 0.0f)
            {
                Person.LocationState = EInvestorPersonLocationState::Indoor;
                Person.TransitionRemainingSeconds = 5.0f;
                Person.VisibilityAlpha = 0.0f;
            }
            break;
        case EInvestorPersonLocationState::Indoor:
            Person.TransitionRemainingSeconds -= DeltaSeconds;
            Person.VisibilityAlpha = 0.0f;
            if (Person.TransitionRemainingSeconds <= 0.0f)
            {
                Person.LocationState = EInvestorPersonLocationState::Exiting;
                Person.TransitionRemainingSeconds = 1.25f;
            }
            break;
        case EInvestorPersonLocationState::Exiting:
            Person.TransitionRemainingSeconds -= DeltaSeconds;
            Person.VisibilityAlpha = FMath::Clamp(
                1.0f - Person.TransitionRemainingSeconds / 1.25f,
                0.0f,
                1.0f);
            if (Person.TransitionRemainingSeconds <= 0.0f)
            {
                Person.LocationState = EInvestorPersonLocationState::Outdoor;
                Person.EntryCooldownRemainingSeconds = 24.0f;
                Person.VisibilityAlpha = 1.0f;
                ++InvestorBuildingExitCount;
                ++InvestorStationReacquisitionCount;
                UE_LOG(
                    LogTemp,
                    Warning,
                    TEXT("INVESTOR_TELECOM_BUILDING_EXIT person=%s rescan=nearest_station"),
                    *GetCentralPersonId(StableIndex));
            }
            break;
        case EInvestorPersonLocationState::Outdoor:
        default:
            Person.VisibilityAlpha = 1.0f;
            break;
        }

        if (Person.LocationState == EInvestorPersonLocationState::Outdoor)
        {
            int32 BestStationIndex = INDEX_NONE;
            float BestDistanceCm = TNumericLimits<float>::Max();
            for (int32 StationIndex = 0;
                 StationIndex < InvestorStations.Num();
                 ++StationIndex)
            {
                const FInvestorStationRuntime& Station =
                    InvestorStations[StationIndex];
                if (!Station.bRoofValidated)
                {
                    continue;
                }
                const float DistanceCm = FVector::Dist2D(
                    PersonLocation,
                    Station.ValidatedRoofPoint);
                if (DistanceCm <= Station.CoverageRadiusCm &&
                    DistanceCm < BestDistanceCm)
                {
                    BestDistanceCm = DistanceCm;
                    BestStationIndex = StationIndex;
                }
            }

            constexpr float HandoverHysteresisCm = 1800.0f;
            if (InvestorStations.IsValidIndex(Person.ServingStationIndex))
            {
                const FInvestorStationRuntime& Current =
                    InvestorStations[Person.ServingStationIndex];
                const float CurrentDistanceCm = FVector::Dist2D(
                    PersonLocation,
                    Current.ValidatedRoofPoint);
                if (Current.bRoofValidated &&
                    CurrentDistanceCm <= Current.CoverageRadiusCm &&
                    (BestStationIndex == INDEX_NONE ||
                     CurrentDistanceCm <=
                        BestDistanceCm + HandoverHysteresisCm))
                {
                    BestStationIndex = Person.ServingStationIndex;
                    BestDistanceCm = CurrentDistanceCm;
                }
            }

            Person.ServingStationIndex = BestStationIndex;
            if (InvestorStations.IsValidIndex(BestStationIndex))
            {
                const float Radius =
                    InvestorStations[BestStationIndex].CoverageRadiusCm;
                Person.SignalQualityPercent = FMath::Clamp(
                    (1.0f - BestDistanceCm / Radius) * 100.0f,
                    8.0f,
                    100.0f);
            }
            else
            {
                Person.SignalQualityPercent = 0.0f;
            }
        }
        else
        {
            Person.ServingStationIndex = INDEX_NONE;
            Person.SignalQualityPercent = 0.0f;
        }

        FTransform VisibleTransform = Transform->GetTransform();
        const float VisibleScale = FMath::Max(Person.VisibilityAlpha, 0.001f);
        VisibleTransform.SetScale3D(FVector(VisibleScale));
        Transform->SetTransform(VisibleTransform);
    }
}

void AOpenMassCrowdSpawner::DrawInvestorAssociationVisuals()
{
    UWorld* World = GetWorld();
    if (!World || !bInvestorDemoInitialized || InvestorPeople.IsEmpty() ||
        !IsValid(InvestorAssociationLineBatch))
    {
        ClearInvestorAssociationVisuals();
        return;
    }

    UMassSpawnerSubsystem* SpawnerSubsystem =
        UWorld::GetSubsystem<UMassSpawnerSubsystem>(World);
    if (!SpawnerSubsystem)
    {
        ClearInvestorAssociationVisuals();
        return;
    }
    FMassEntityManager& EntityManager =
        SpawnerSubsystem->GetEntityManagerChecked();

    // Build the complete connected snapshot in one component update. Every
    // eligible outdoor person is represented on every refresh; there is no
    // rotating phase or short lifetime that can make a valid link blink out.
    TArray<FBatchedLine> Lines;
    Lines.Reserve(InvestorPeople.Num() * 32);
    int32 SourceConnectedCount = 0;
    int32 RenderedLinkCount = 0;
    int32 RenderedDashedLinkCount = 0;
    const FLinearColor SelectedColor = FLinearColor::FromSRGBColor(
        FColor(55, 125, 165, 135));
    const FLinearColor OtherColor = FLinearColor::FromSRGBColor(
        FColor(95, 102, 110, 80));

    for (int32 StableIndex = 0;
         StableIndex < InvestorPeople.Num();
         ++StableIndex)
    {
        const FInvestorPersonRuntime& Person = InvestorPeople[StableIndex];
        if (!InvestorStations.IsValidIndex(Person.ServingStationIndex) ||
            !SpawnedEntities.IsValidIndex(StableIndex))
        {
            continue;
        }
        const FInvestorStationRuntime& Station =
            InvestorStations[Person.ServingStationIndex];
        if (!Station.bRoofValidated)
        {
            continue;
        }

        const bool bSelected =
            StableIndex == SelectedCentralProfileEntityIndex;
        const FMassEntityHandle Entity = SpawnedEntities[StableIndex];
        if (!EntityManager.IsEntityValid(Entity))
        {
            continue;
        }
        const FTransformFragment* Transform =
            EntityManager.GetFragmentDataPtr<FTransformFragment>(Entity);
        if (!Transform)
        {
            continue;
        }

        const FVector PersonPoint =
            Transform->GetTransform().GetLocation() + FVector(0.0, 0.0, 100.0);
        const FVector StationPoint =
            Station.ValidatedRoofPoint +
            FVector(0.0, 0.0, InvestorAssociationRoofOffsetCm);
        ++SourceConnectedCount;

        if (bSelected)
        {
            Lines.Emplace(
                PersonPoint,
                StationPoint,
                SelectedColor,
                0.0f,
                2.25f,
                1,
                static_cast<uint32>(StableIndex + 1));
            ++RenderedLinkCount;
            continue;
        }

        const FVector LinkVector = StationPoint - PersonPoint;
        const float LinkLength = LinkVector.Size();
        if (LinkLength <= UE_SMALL_NUMBER)
        {
            continue;
        }
        const FVector LinkDirection = LinkVector / LinkLength;
        const float DashStep =
            InvestorAssociationDashLengthCm + InvestorAssociationDashGapCm;
        for (float DashStart = 0.0f;
             DashStart < LinkLength;
             DashStart += DashStep)
        {
            const float DashEnd = FMath::Min(
                DashStart + InvestorAssociationDashLengthCm,
                LinkLength);
            Lines.Emplace(
                PersonPoint + LinkDirection * DashStart,
                PersonPoint + LinkDirection * DashEnd,
                OtherColor,
                0.0f,
                1.0f,
                1,
                static_cast<uint32>(StableIndex + 1));
        }
        ++RenderedLinkCount;
        ++RenderedDashedLinkCount;
    }

    InvestorAssociationLineBatch->Flush();
    InvestorAssociationLineBatch->SetVisibility(true, true);
    if (!Lines.IsEmpty())
    {
        InvestorAssociationLineBatch->DrawLines(MakeArrayView(Lines));
    }
    InvestorAssociationSourceConnectedCount = SourceConnectedCount;
    InvestorAssociationRenderedLinkCount = RenderedLinkCount;
    InvestorAssociationRenderedDashedLinkCount = RenderedDashedLinkCount;
    InvestorAssociationRenderedSegmentCount = Lines.Num();
    ++InvestorAssociationVisualRevision;
}

void AOpenMassCrowdSpawner::ClearInvestorAssociationVisuals()
{
    if (IsValid(InvestorAssociationLineBatch))
    {
        InvestorAssociationLineBatch->Flush();
        InvestorAssociationLineBatch->SetVisibility(false, true);
    }
    InvestorAssociationSourceConnectedCount = 0;
    InvestorAssociationRenderedLinkCount = 0;
    InvestorAssociationRenderedDashedLinkCount = 0;
    InvestorAssociationRenderedSegmentCount = 0;
}

bool AOpenMassCrowdSpawner::ValidateInvestorPersistedSignalLayer()
{
    InvestorPersistedSignalActorCount = 0;
    InvestorPersistedSignalVisibleActorCount = 0;
    InvestorPersistedSignalSourceCount = 0;
    InvestorPersistedSignalRayCount = 0;
    bInvestorPersistedSignalLayerReady = false;
    if (!GetWorld())
    {
        return false;
    }

    bool bComponentsValid = true;
    for (TActorIterator<AActor> It(GetWorld()); It; ++It)
    {
        AActor* Actor = *It;
        const FString Identity = GetSignalActorLabel(Actor);
        if (!IsPersistedRooftopSignalActorLabel(Identity))
        {
            continue;
        }

        ++InvestorPersistedSignalActorCount;
        InvestorPersistedSignalSourceCount +=
            Identity.StartsWith(TEXT("SIG_Source_")) ? 1 : 0;
        InvestorPersistedSignalRayCount +=
            Identity.StartsWith(TEXT("SIG_Ray_")) ? 1 : 0;

        TArray<UStaticMeshComponent*> StaticMeshComponents;
        Actor->GetComponents<UStaticMeshComponent>(StaticMeshComponents);
        if (StaticMeshComponents.Num() != 1 ||
            !IsValid(StaticMeshComponents[0]) ||
            !IsValid(StaticMeshComponents[0]->GetStaticMesh()))
        {
            bComponentsValid = false;
            UE_LOG(
                LogTemp,
                Error,
                TEXT("INVESTOR_PERSISTED_SIGNAL_INVALID actor=%s static_mesh_components=%d"),
                *Identity,
                StaticMeshComponents.Num());
            continue;
        }

        UStaticMeshComponent* Component = StaticMeshComponents[0];
        const bool bVisible =
            !Actor->IsHidden() &&
            Component->IsVisible() &&
            !Component->bHiddenInGame;
        InvestorPersistedSignalVisibleActorCount += bVisible ? 1 : 0;
    }

    bInvestorPersistedSignalLayerReady =
        bComponentsValid &&
        InvestorPersistedSignalActorCount == InvestorExpectedSignalActorCount &&
        InvestorPersistedSignalVisibleActorCount ==
            InvestorExpectedSignalActorCount &&
        InvestorPersistedSignalSourceCount ==
            InvestorExpectedSignalSourceCount &&
        InvestorPersistedSignalRayCount == InvestorExpectedSignalRayCount;
    UE_LOG(
        LogTemp,
        Warning,
        TEXT("INVESTOR_PERSISTED_SIGNAL_READY ready=%s actors=%d visible=%d sources=%d rays=%d modified=false runtime_rebuild=false"),
        bInvestorPersistedSignalLayerReady ? TEXT("true") : TEXT("false"),
        InvestorPersistedSignalActorCount,
        InvestorPersistedSignalVisibleActorCount,
        InvestorPersistedSignalSourceCount,
        InvestorPersistedSignalRayCount);
    return bInvestorPersistedSignalLayerReady;
}

void AOpenMassCrowdSpawner::RegisterInvestorLegacySignalGuards()
{
    UWorld* World = GetWorld();
    if (!World || !World->IsGameWorld())
    {
        return;
    }
    if (!InvestorActorSpawnedDelegateHandle.IsValid())
    {
        InvestorActorSpawnedDelegateHandle = World->AddOnActorSpawnedHandler(
            FOnActorSpawned::FDelegate::CreateUObject(
                this,
                &AOpenMassCrowdSpawner::HandleInvestorActorSpawned));
    }
    if (!InvestorLevelAddedDelegateHandle.IsValid())
    {
        InvestorLevelAddedDelegateHandle =
            FWorldDelegates::LevelAddedToWorld.AddUObject(
                this,
                &AOpenMassCrowdSpawner::HandleInvestorLevelAdded);
    }
}

void AOpenMassCrowdSpawner::UnregisterInvestorLegacySignalGuards()
{
    if (UWorld* World = GetWorld();
        World && InvestorActorSpawnedDelegateHandle.IsValid())
    {
        World->RemoveOnActorSpawnedHandler(
            InvestorActorSpawnedDelegateHandle);
    }
    InvestorActorSpawnedDelegateHandle.Reset();
    if (InvestorLevelAddedDelegateHandle.IsValid())
    {
        FWorldDelegates::LevelAddedToWorld.Remove(
            InvestorLevelAddedDelegateHandle);
        InvestorLevelAddedDelegateHandle.Reset();
    }
}

void AOpenMassCrowdSpawner::HandleInvestorActorSpawned(AActor* Actor)
{
    if (!bInvestorDeliveryDemoEnabled || !IsValid(Actor) ||
        Actor->GetWorld() != GetWorld() ||
        !IsLegacyFloatingSignalActorLabel(GetSignalActorLabel(Actor)))
    {
        return;
    }
    HideLegacyFloatingSignalActor(Actor);
}

void AOpenMassCrowdSpawner::HandleInvestorLevelAdded(
    ULevel* Level,
    UWorld* World)
{
    if (bInvestorDeliveryDemoEnabled && IsValid(Level) &&
        World == GetWorld())
    {
        // World Partition has finished constructing/registering this cell's
        // components, so close the small interval in which a legacy channel
        // could otherwise become renderable before the periodic guard runs.
        SuppressLegacyFloatingSignalActors();
    }
}

void AOpenMassCrowdSpawner::SuppressLegacyFloatingSignalActors()
{
    UWorld* World = GetWorld();
    if (!World || !World->IsGameWorld())
    {
        InvestorLegacyFloatingSignalLoadedCount = 0;
        InvestorLegacyFloatingSignalVisibleCount = 0;
        return;
    }

    int32 LoadedCount = 0;
    int32 NewlySuppressedCount = 0;
    int32 VisibleAfterCount = 0;
    for (TActorIterator<AActor> It(World); It; ++It)
    {
        AActor* Actor = *It;
        if (!IsValid(Actor) ||
            !IsLegacyFloatingSignalActorLabel(GetSignalActorLabel(Actor)))
        {
            continue;
        }

        ++LoadedCount;
        const bool bWasVisible = HideLegacyFloatingSignalActor(Actor);
        if (bWasVisible)
        {
            ++NewlySuppressedCount;
        }
        VisibleAfterCount +=
            IsLegacyFloatingSignalActorVisible(Actor) ? 1 : 0;
    }

    InvestorLegacyFloatingSignalLoadedCount = LoadedCount;
    InvestorLegacyFloatingSignalVisibleCount = VisibleAfterCount;
    if (NewlySuppressedCount > 0 || VisibleAfterCount > 0)
    {
        UE_LOG(
            LogTemp,
            Warning,
            TEXT("INVESTOR_LEGACY_FLOATING_SIGNAL_SUPPRESSION loaded=%d newly_hidden=%d visible_after=%d"),
            LoadedCount,
            NewlySuppressedCount,
            VisibleAfterCount);
    }
}

int32 AOpenMassCrowdSpawner::GetInvestorConnectedCount() const
{
    int32 Count = 0;
    for (const FInvestorPersonRuntime& Person : InvestorPeople)
    {
        Count += Person.ServingStationIndex != INDEX_NONE ? 1 : 0;
    }
    return Count;
}

int32 AOpenMassCrowdSpawner::GetInvestorIndoorCount() const
{
    int32 Count = 0;
    for (const FInvestorPersonRuntime& Person : InvestorPeople)
    {
        Count += Person.LocationState != EInvestorPersonLocationState::Outdoor
            ? 1
            : 0;
    }
    return Count;
}

int32 AOpenMassCrowdSpawner::GetInvestorValidatedStationCount() const
{
    int32 Count = 0;
    for (const FInvestorStationRuntime& Station : InvestorStations)
    {
        Count += Station.bRoofValidated ? 1 : 0;
    }
    return Count;
}

FString AOpenMassCrowdSpawner::GetInvestorPersonLocationLabel(
    const int32 StableEntityIndex) const
{
    if (!InvestorPeople.IsValidIndex(StableEntityIndex))
    {
        return TEXT("OUTDOOR · INITIALIZING");
    }
    switch (InvestorPeople[StableEntityIndex].LocationState)
    {
    case EInvestorPersonLocationState::Entering:
        return TEXT("ENTERING BUILDING · 正在进入");
    case EInvestorPersonLocationState::Indoor:
        return TEXT("INDOOR · 室内（外部基站已断开）");
    case EInvestorPersonLocationState::Exiting:
        return TEXT("EXITING BUILDING · 正在离开");
    case EInvestorPersonLocationState::Outdoor:
    default:
        return TEXT("OUTDOOR · CENTRAL STREET");
    }
}

FString AOpenMassCrowdSpawner::GetInvestorPersonStationLabel(
    const int32 StableEntityIndex) const
{
    if (!InvestorPeople.IsValidIndex(StableEntityIndex))
    {
        return TEXT("SCANNING");
    }
    const int32 StationIndex =
        InvestorPeople[StableEntityIndex].ServingStationIndex;
    return InvestorStations.IsValidIndex(StationIndex)
        ? InvestorStations[StationIndex].StationId.ToString()
        : TEXT("DISCONNECTED");
}

FString AOpenMassCrowdSpawner::GetInvestorPersonSignalLabel(
    const int32 StableEntityIndex) const
{
    if (!InvestorPeople.IsValidIndex(StableEntityIndex) ||
        InvestorPeople[StableEntityIndex].ServingStationIndex == INDEX_NONE)
    {
        return TEXT("0% · NO EXTERNAL SIGNAL");
    }
    const float Quality =
        InvestorPeople[StableEntityIndex].SignalQualityPercent;
    const TCHAR* QualityLabel = Quality >= 70.0f
        ? TEXT("EXCELLENT")
        : (Quality >= 40.0f ? TEXT("GOOD") : TEXT("EDGE"));
    return FString::Printf(TEXT("%.0f%% · %s"), Quality, QualityLabel);
}

FString AOpenMassCrowdSpawner::GetInvestorPersonApplication(
    const int32 StableEntityIndex) const
{
    static const TCHAR* Apps[] = {
        TEXT("Octopus 八达通"), TEXT("MTR Mobile"), TEXT("Citymapper"),
        TEXT("WhatsApp"), TEXT("WeChat"), TEXT("Microsoft Teams"),
        TEXT("ArcGIS Field Maps"), TEXT("HK Observatory")};
    if (!InvestorPeople.IsValidIndex(StableEntityIndex))
    {
        return TEXT("INITIALIZING");
    }
    const FInvestorPersonRuntime& Person = InvestorPeople[StableEntityIndex];
    if (Person.LocationState == EInvestorPersonLocationState::Indoor)
    {
        return TEXT("Microsoft Teams · INDOOR");
    }
    const int32 BaseIndex = Person.CurrentApplicationIndex;
    const int32 StationOffset = FMath::Max(Person.ServingStationIndex, 0);
    return Apps[(BaseIndex + StationOffset) % UE_ARRAY_COUNT(Apps)];
}

void AOpenMassCrowdSpawner::ShowInvestorKPI()
{
    if (InvestorKPIViewportWidget.IsValid() ||
        !GEngine || !GEngine->GameViewport)
    {
        return;
    }
    const TWeakObjectPtr<AOpenMassCrowdSpawner> WeakThis(this);
    TSharedRef<SWidget> KPI =
        SNew(SConstraintCanvas)
        + SConstraintCanvas::Slot()
        .Anchors(FAnchors(1.0f, 0.0f))
        .Alignment(FVector2D::ZeroVector)
        .Offset(FMargin(-1050.0f, 28.0f, 620.0f, 90.0f))
        .AutoSize(false)
        [
            SNew(SBorder)
            .BorderImage(FCoreStyle::Get().GetBrush("WhiteBrush"))
            .BorderBackgroundColor(FLinearColor(0.008f, 0.025f, 0.045f, 0.86f))
            .Padding(FMargin(18.0f, 11.0f))
            [
                SNew(SVerticalBox)
                + SVerticalBox::Slot()
                .AutoHeight()
                [
                    SNew(STextBlock)
                    .Text(FText::FromString(TEXT("CENTRAL LIVE DIGITAL TWIN")))
                    .Font(FCoreStyle::GetDefaultFontStyle("Bold", 12))
                    .ColorAndOpacity(FLinearColor(0.10f, 0.88f, 0.96f, 1.0f))
                ]
                + SVerticalBox::Slot()
                .AutoHeight()
                .Padding(0.0f, 5.0f, 0.0f, 0.0f)
                [
                    SNew(STextBlock)
                    .Text_Lambda([WeakThis]()
                    {
                        if (!WeakThis.IsValid())
                        {
                            return FText::GetEmpty();
                        }
                        return FText::FromString(FString::Printf(
                            TEXT("PEOPLE  %d   |   CONNECTED  %d   |   INDOOR  %d   |   ROOFTOP NODES  %d/2"),
                            WeakThis->InvestorPeople.Num(),
                            WeakThis->GetInvestorConnectedCount(),
                            WeakThis->GetInvestorIndoorCount(),
                            WeakThis->GetInvestorValidatedStationCount()));
                    })
                    .Font(FCoreStyle::GetDefaultFontStyle("Bold", 10))
                    .ColorAndOpacity(FLinearColor(0.88f, 0.96f, 1.0f, 1.0f))
                ]
            ]
        ];
    InvestorKPIViewportWidget = KPI;
    APlayerController* PlayerController =
        UGameplayStatics::GetPlayerController(this, 0);
    if (ULocalPlayer* LocalPlayer = PlayerController
            ? PlayerController->GetLocalPlayer()
            : nullptr)
    {
        GEngine->GameViewport->AddViewportWidgetForPlayer(
            LocalPlayer,
            KPI,
            900);
    }
    else
    {
        GEngine->GameViewport->AddViewportWidgetContent(KPI, 900);
    }
}

void AOpenMassCrowdSpawner::HideInvestorKPI()
{
    if (InvestorKPIViewportWidget.IsValid() &&
        GEngine && GEngine->GameViewport)
    {
        APlayerController* PlayerController =
            UGameplayStatics::GetPlayerController(this, 0);
        if (ULocalPlayer* LocalPlayer = PlayerController
                ? PlayerController->GetLocalPlayer()
                : nullptr)
        {
            GEngine->GameViewport->RemoveViewportWidgetForPlayer(
                LocalPlayer,
                InvestorKPIViewportWidget.ToSharedRef());
        }
        else
        {
            GEngine->GameViewport->RemoveViewportWidgetContent(
                InvestorKPIViewportWidget.ToSharedRef());
        }
    }
    InvestorKPIViewportWidget.Reset();
}

bool AOpenMassCrowdSpawner::ShowCentralProfileByStableIndex(
    const int32 StableEntityIndex)
{
    if (NetworkMode != EOpenMassCrowdNetworkMode::CentralCertifiedCache ||
        !SpawnedEntities.IsValidIndex(StableEntityIndex) ||
        !GEngine || !GEngine->GameViewport)
    {
        return false;
    }

    UMassSpawnerSubsystem* SpawnerSubsystem =
        UWorld::GetSubsystem<UMassSpawnerSubsystem>(GetWorld());
    if (!SpawnerSubsystem)
    {
        return false;
    }
    FMassEntityManager& EntityManager =
        SpawnerSubsystem->GetEntityManagerChecked();
    if (!EntityManager.IsEntityValid(SpawnedEntities[StableEntityIndex]))
    {
        return false;
    }

    HideCentralProfile();
    SelectedCentralProfileEntityIndex = StableEntityIndex;

    const FString PersonId = GetCentralPersonId(StableEntityIndex);
    const FString PersonName = GetCentralPersonName(StableEntityIndex);
    const FString Occupation = GetCentralPersonOccupation(StableEntityIndex);
    const FString Software = GetCentralPersonSoftware(StableEntityIndex);
    const FString GenderAge = FString::Printf(
        TEXT("%s    %d 岁"),
        *GetCentralPersonGender(StableEntityIndex),
        GetCentralPersonAge(StableEntityIndex));
    const FString CurrentApplication =
        GetInvestorPersonApplication(StableEntityIndex);
    const FString LocationState =
        GetInvestorPersonLocationLabel(StableEntityIndex);
    const FString ServingStation =
        GetInvestorPersonStationLabel(StableEntityIndex);
    const FString SignalQuality =
        GetInvestorPersonSignalLabel(StableEntityIndex);
    FString DistrictName = TEXT("CENTRAL");
    if (CentralSpawnDistrictPlan.IsValidIndex(StableEntityIndex))
    {
        const int32 DistrictIndex = CentralSpawnDistrictPlan[StableEntityIndex];
        if (RuntimeCentralDistricts.IsValidIndex(DistrictIndex))
        {
            DistrictName = RuntimeCentralDistricts[DistrictIndex].DistrictId.ToString();
        }
    }

    FString RouteStatus = TEXT("路径准备中");
    FString RouteDistance = TEXT("正在建立认证路线");
    if (EntityRouteStates.IsValidIndex(StableEntityIndex))
    {
        const FEntityRouteState& Route = EntityRouteStates[StableEntityIndex];
        RouteStatus = Route.bOnReturnLeg ? TEXT("返程中 · RETURN") :
            TEXT("去程中 · OUTBOUND");
        if (Route.PlannedRoundTripDistanceCm > 0.0f)
        {
            RouteDistance = FString::Printf(
                TEXT("%.1f m 往返 · 已完成 %d 轮"),
                Route.PlannedRoundTripDistanceCm / 100.0f,
                Route.CompletedRoundTrips);
        }
    }

    const FLinearColor Cyan(0.04f, 0.78f, 0.88f, 1.0f);
    const FLinearColor Amber(1.0f, 0.68f, 0.16f, 1.0f);
    const FLinearColor Green(0.25f, 0.94f, 0.63f, 1.0f);
    const bool bConnected = InvestorPeople.IsValidIndex(StableEntityIndex) &&
        InvestorPeople[StableEntityIndex].ServingStationIndex != INDEX_NONE;
    const bool bIndoor = InvestorPeople.IsValidIndex(StableEntityIndex) &&
        InvestorPeople[StableEntityIndex].LocationState !=
            EInvestorPersonLocationState::Outdoor;
    const FLinearColor LiveColor = bIndoor ? Amber : (bConnected ? Cyan :
        FLinearColor(1.0f, 0.34f, 0.28f, 1.0f));
    const FString LiveStatus = bIndoor
        ? TEXT("●  INDOOR · EXTERNAL LINK DISCONNECTED")
        : (bConnected
            ? TEXT("●  LIVE · NETWORK ASSOCIATION ACTIVE")
            : TEXT("●  OUTDOOR · SEARCHING FOR NETWORK"));
    const TWeakObjectPtr<AOpenMassCrowdSpawner> WeakThis(this);

    TSharedRef<SWidget> ProfilePanel =
        SNew(SConstraintCanvas)
        + SConstraintCanvas::Slot()
        .Anchors(FAnchors(0.0f, 1.0f))
        .Alignment(FVector2D::ZeroVector)
        .Offset(FMargin(36.0f, -700.0f, 480.0f, 660.0f))
        .AutoSize(false)
        [
            SNew(SBox)
            .WidthOverride(440.0f)
            [
                SNew(SBorder)
                .BorderImage(FCoreStyle::Get().GetBrush("WhiteBrush"))
                .BorderBackgroundColor(FLinearColor(0.012f, 0.026f, 0.042f, 0.91f))
                .Padding(0.0f)
                [
                    SNew(SVerticalBox)
                    + SVerticalBox::Slot()
                    .AutoHeight()
                    [
                        SNew(SBorder)
                        .BorderImage(FCoreStyle::Get().GetBrush("WhiteBrush"))
                        .BorderBackgroundColor(FLinearColor(0.025f, 0.22f, 0.28f, 0.96f))
                        .Padding(FMargin(20.0f, 12.0f))
                        [
                            SNew(SHorizontalBox)
                            + SHorizontalBox::Slot()
                            .FillWidth(1.0f)
                            .VAlign(VAlign_Center)
                            [
                                SNew(SVerticalBox)
                                + SVerticalBox::Slot()
                                .AutoHeight()
                                [
                                    SNew(STextBlock)
                                    .Text(FText::FromString(TEXT("TELECOMTWIN · CENTRAL LIVE")))
                                    .Font(FCoreStyle::GetDefaultFontStyle("Bold", 12))
                                    .ColorAndOpacity(Cyan)
                                ]
                                + SVerticalBox::Slot()
                                .AutoHeight()
                                .Padding(0.0f, 2.0f, 0.0f, 0.0f)
                                [
                                    SNew(STextBlock)
                                    .Text(FText::FromString(TEXT("SELECTED PERSON / 实时人物档案")))
                                    .Font(FCoreStyle::GetDefaultFontStyle("Regular", 9))
                                    .ColorAndOpacity(FLinearColor(0.65f, 0.82f, 0.86f, 1.0f))
                                ]
                            ]
                            + SHorizontalBox::Slot()
                            .AutoWidth()
                            .VAlign(VAlign_Center)
                            [
                                SNew(SButton)
                                .ButtonColorAndOpacity(FLinearColor(0.08f, 0.12f, 0.15f, 0.8f))
                                .ContentPadding(FMargin(10.0f, 5.0f))
                                .OnClicked_Lambda([WeakThis]()
                                {
                                    if (WeakThis.IsValid())
                                    {
                                        WeakThis->HideCentralProfile();
                                    }
                                    return FReply::Handled();
                                })
                                [
                                    SNew(STextBlock)
                                    .Text(FText::FromString(TEXT("关闭  ×")))
                                    .Font(FCoreStyle::GetDefaultFontStyle("Bold", 10))
                                    .ColorAndOpacity(FLinearColor::White)
                                ]
                            ]
                        ]
                    ]
                    + SVerticalBox::Slot()
                    .AutoHeight()
                    .Padding(FMargin(22.0f, 14.0f, 22.0f, 6.0f))
                    [
                        SNew(SVerticalBox)
                        + SVerticalBox::Slot()
                        .AutoHeight()
                        [
                            SNew(STextBlock)
                            .Text(FText::FromString(PersonId))
                            .Font(FCoreStyle::GetDefaultFontStyle("Bold", 11))
                            .ColorAndOpacity(Amber)
                        ]
                        + SVerticalBox::Slot()
                        .AutoHeight()
                        .Padding(0.0f, 3.0f, 0.0f, 0.0f)
                        [
                            SNew(STextBlock)
                            .Text(FText::FromString(PersonName))
                            .Font(FCoreStyle::GetDefaultFontStyle("Bold", 30))
                            .ColorAndOpacity(FLinearColor(0.96f, 0.99f, 1.0f, 1.0f))
                        ]
                        + SVerticalBox::Slot()
                        .AutoHeight()
                        .Padding(0.0f, 10.0f, 0.0f, 0.0f)
                        [
                            SNew(SBorder)
                            .BorderImage(FCoreStyle::Get().GetBrush("WhiteBrush"))
                            .BorderBackgroundColor(FLinearColor(
                                LiveColor.R * 0.13f,
                                LiveColor.G * 0.13f,
                                LiveColor.B * 0.13f,
                                0.88f))
                            .Padding(FMargin(10.0f, 6.0f))
                            [
                                SNew(STextBlock)
                                .Text(FText::FromString(LiveStatus))
                                .Font(FCoreStyle::GetDefaultFontStyle("Bold", 10))
                                .ColorAndOpacity(LiveColor)
                            ]
                        ]
                    ]
                    + SVerticalBox::Slot()
                    .AutoHeight()
                    .Padding(FMargin(22.0f, 2.0f))
                    [MakeCentralProfileRow(TEXT("◈  职业 / OCCUPATION"), Occupation, Cyan)]
                    + SVerticalBox::Slot()
                    .AutoHeight()
                    .Padding(FMargin(22.0f, 2.0f))
                    [MakeCentralProfileRow(TEXT("♀  性别与年龄 / GENDER & AGE"), GenderAge, Amber)]
                    + SVerticalBox::Slot()
                    .AutoHeight()
                    .Padding(FMargin(22.0f, 2.0f))
                    [MakeCentralProfileRow(TEXT("◉  当前应用 / LIVE APP"), CurrentApplication, Cyan)]
                    + SVerticalBox::Slot()
                    .AutoHeight()
                    .Padding(FMargin(22.0f, 2.0f))
                    [MakeCentralProfileRow(TEXT("⌂  空间状态 / LOCATION"), LocationState, Green)]
                    + SVerticalBox::Slot()
                    .AutoHeight()
                    .Padding(FMargin(22.0f, 2.0f))
                    [MakeCentralProfileRow(TEXT("⌁  服务基站 / SERVING NODE"), ServingStation, Cyan)]
                    + SVerticalBox::Slot()
                    .AutoHeight()
                    .Padding(FMargin(22.0f, 2.0f, 22.0f, 14.0f))
                    [MakeCentralProfileRow(TEXT("▮▮▮  信号质量 / SIGNAL"), SignalQuality, Amber)]
                ]
            ]
        ];

    CentralProfileViewportWidget = ProfilePanel;
    APlayerController* PlayerController =
        UGameplayStatics::GetPlayerController(this, 0);
    if (ULocalPlayer* LocalPlayer = PlayerController
            ? PlayerController->GetLocalPlayer()
            : nullptr)
    {
        GEngine->GameViewport->AddViewportWidgetForPlayer(
            LocalPlayer,
            ProfilePanel,
            1000);
    }
    else
    {
        GEngine->GameViewport->AddViewportWidgetContent(ProfilePanel, 1000);
    }
    UE_LOG(
        LogTemp,
        Warning,
        TEXT("OPEN_MASS_CROWD_CENTRAL_PROFILE_SELECTED stable_index=%d person_id=%s name=%s occupation=%s software=%s district=%s route=%s location=%s station=%s signal=%s app=%s"),
        StableEntityIndex,
        *PersonId,
        *PersonName,
        *Occupation,
        *Software,
        *DistrictName,
        *RouteStatus,
        *LocationState,
        *ServingStation,
        *SignalQuality,
        *CurrentApplication);
    return true;
}

void AOpenMassCrowdSpawner::HideCentralProfile()
{
    if (CentralProfileViewportWidget.IsValid() &&
        GEngine && GEngine->GameViewport)
    {
        APlayerController* PlayerController =
            UGameplayStatics::GetPlayerController(this, 0);
        if (ULocalPlayer* LocalPlayer = PlayerController
                ? PlayerController->GetLocalPlayer()
                : nullptr)
        {
            GEngine->GameViewport->RemoveViewportWidgetForPlayer(
                LocalPlayer,
                CentralProfileViewportWidget.ToSharedRef());
        }
        else
        {
            GEngine->GameViewport->RemoveViewportWidgetContent(
                CentralProfileViewportWidget.ToSharedRef());
        }
    }
    CentralProfileViewportWidget.Reset();
    SelectedCentralProfileEntityIndex = INDEX_NONE;
}

void AOpenMassCrowdSpawner::UpdateCentralProfileInteraction()
{
    if (!bCentralAdmissionReleased || !GetWorld())
    {
        return;
    }

    APlayerController* PlayerController =
        UGameplayStatics::GetPlayerController(this, 0);
    if (!PlayerController)
    {
        return;
    }
    if (!bCentralProfileInputConfigured)
    {
        PlayerController->bShowMouseCursor = true;
        PlayerController->bEnableClickEvents = true;
        PlayerController->bEnableMouseOverEvents = true;
        FInputModeGameAndUI InputMode;
        InputMode.SetHideCursorDuringCapture(false);
        InputMode.SetLockMouseToViewportBehavior(
            EMouseLockMode::LockOnCapture);
        PlayerController->SetInputMode(InputMode);
        bCentralProfileInputConfigured = true;
        UE_LOG(
            LogTemp,
            Warning,
            TEXT("OPEN_MASS_CROWD_CENTRAL_PROFILE_INPUT_READY stable_profiles=%d selection=mass_screen_space investor_lower_left=true"),
            SpawnedEntities.Num());
    }

    UMassSpawnerSubsystem* SpawnerSubsystem =
        UWorld::GetSubsystem<UMassSpawnerSubsystem>(GetWorld());
    if (SelectedCentralProfileEntityIndex != INDEX_NONE && SpawnerSubsystem &&
        SpawnedEntities.IsValidIndex(SelectedCentralProfileEntityIndex))
    {
        FMassEntityManager& EntityManager =
            SpawnerSubsystem->GetEntityManagerChecked();
        const FMassEntityHandle SelectedEntity =
            SpawnedEntities[SelectedCentralProfileEntityIndex];
        if (EntityManager.IsEntityValid(SelectedEntity))
        {
            if (const FTransformFragment* Transform =
                EntityManager.GetFragmentDataPtr<FTransformFragment>(SelectedEntity))
            {
                const FVector MarkerLocation =
                    Transform->GetTransform().GetLocation() + FVector(0.0, 0.0, 105.0);
                DrawDebugSphere(
                    GetWorld(),
                    MarkerLocation,
                    28.0f,
                    16,
                    FColor(25, 225, 245),
                    false,
                    -1.0f,
                    0,
                    2.5f);
            }
        }
    }

    const bool bSelectionClick =
        PlayerController->WasInputKeyJustPressed(EKeys::LeftMouseButton);
    if (!bSelectionClick || !SpawnerSubsystem)
    {
        CentralProfilePreviousControlRotation =
            PlayerController->GetControlRotation();
        bCentralProfileHasPreviousControlRotation = true;
        return;
    }

    float MouseX = 0.0f;
    float MouseY = 0.0f;
    if (!PlayerController->GetMousePosition(MouseX, MouseY))
    {
        return;
    }
    int32 ViewportWidth = 0;
    int32 ViewportHeight = 0;
    PlayerController->GetViewportSize(ViewportWidth, ViewportHeight);
    if (CentralProfileViewportWidget.IsValid() &&
        MouseX <= 500.0f &&
        MouseY >= static_cast<float>(ViewportHeight) - 680.0f)
    {
        return;
    }

    FMassEntityManager& EntityManager =
        SpawnerSubsystem->GetEntityManagerChecked();
    int32 BestStableIndex = INDEX_NONE;
    float BestScreenDistanceSquared = FMath::Square(42.0f);
    float BestCameraDistanceSquared = TNumericLimits<float>::Max();
    const FVector CameraLocation = PlayerController->PlayerCameraManager
        ? PlayerController->PlayerCameraManager->GetCameraLocation()
        : FVector::ZeroVector;
    for (int32 StableIndex = 0;
         StableIndex < SpawnedEntities.Num();
         ++StableIndex)
    {
        const FMassEntityHandle Entity = SpawnedEntities[StableIndex];
        if (!EntityManager.IsEntityValid(Entity))
        {
            continue;
        }
        const FTransformFragment* Transform =
            EntityManager.GetFragmentDataPtr<FTransformFragment>(Entity);
        if (!Transform)
        {
            continue;
        }
        const FVector WorldLocation =
            Transform->GetTransform().GetLocation() + FVector(0.0, 0.0, 85.0);
        FVector2D ScreenLocation;
        if (!PlayerController->ProjectWorldLocationToScreen(
                WorldLocation,
                ScreenLocation,
                true))
        {
            continue;
        }
        const float ScreenDistanceSquared = FVector2D::DistSquared(
            ScreenLocation,
            FVector2D(MouseX, MouseY));
        const float CameraDistanceSquared = FVector::DistSquared(
            CameraLocation,
            WorldLocation);
        if (ScreenDistanceSquared < BestScreenDistanceSquared ||
            (FMath::IsNearlyEqual(
                 ScreenDistanceSquared,
                 BestScreenDistanceSquared,
                 0.25f) &&
             CameraDistanceSquared < BestCameraDistanceSquared))
        {
            BestStableIndex = StableIndex;
            BestScreenDistanceSquared = ScreenDistanceSquared;
            BestCameraDistanceSquared = CameraDistanceSquared;
        }
    }

    if (BestStableIndex != INDEX_NONE)
    {
        ShowCentralProfileByStableIndex(BestStableIndex);
        if (bCentralProfileHasPreviousControlRotation)
        {
            PlayerController->SetControlRotation(
                CentralProfilePreviousControlRotation);
            if (APawn* Pawn = PlayerController->GetPawn())
            {
                FRotator PawnRotation = Pawn->GetActorRotation();
                PawnRotation.Pitch = CentralProfilePreviousControlRotation.Pitch;
                PawnRotation.Yaw = CentralProfilePreviousControlRotation.Yaw;
                Pawn->SetActorRotation(PawnRotation);
            }
        }
    }
    CentralProfilePreviousControlRotation = PlayerController->GetControlRotation();
    bCentralProfileHasPreviousControlRotation = true;
}

FString AOpenMassCrowdSpawner::GetCentralProfileEvidenceSnapshot() const
{
    const int32 StableIndex = SelectedCentralProfileEntityIndex;
    if (!SpawnedEntities.IsValidIndex(StableIndex))
    {
        return TEXT("{\"valid\":false,\"reason\":\"no_profile_selected\"}");
    }

    FString DistrictName = TEXT("CENTRAL");
    if (CentralSpawnDistrictPlan.IsValidIndex(StableIndex))
    {
        const int32 DistrictIndex = CentralSpawnDistrictPlan[StableIndex];
        if (RuntimeCentralDistricts.IsValidIndex(DistrictIndex))
        {
            DistrictName = RuntimeCentralDistricts[DistrictIndex].DistrictId.ToString();
        }
    }
    const FEntityRouteState* Route = EntityRouteStates.IsValidIndex(StableIndex)
        ? &EntityRouteStates[StableIndex]
        : nullptr;
    return FString::Printf(
        TEXT("{\"valid\":true,\"stable_index\":%d,\"person_id\":\"%s\",\"name\":\"%s\",\"occupation\":\"%s\",\"gender\":\"%s\",\"age\":%d,\"favorite_software\":\"%s\",\"current_app\":\"%s\",\"district\":\"%s\",\"location_state\":\"%s\",\"serving_station\":\"%s\",\"signal_quality\":\"%s\",\"route_leg\":\"%s\",\"round_trip_m\":%.3f,\"completed_round_trips\":%d,\"glass_panel_visible\":%s,\"panel_anchor\":\"lower_left\"}"),
        StableIndex,
        *GetCentralPersonId(StableIndex),
        *GetCentralPersonName(StableIndex),
        *GetCentralPersonOccupation(StableIndex),
        *GetCentralPersonGender(StableIndex),
        GetCentralPersonAge(StableIndex),
        *GetCentralPersonSoftware(StableIndex),
        *GetInvestorPersonApplication(StableIndex),
        *DistrictName,
        *GetInvestorPersonLocationLabel(StableIndex),
        *GetInvestorPersonStationLabel(StableIndex),
        *GetInvestorPersonSignalLabel(StableIndex),
        Route && Route->bOnReturnLeg ? TEXT("return") : TEXT("outbound"),
        Route ? Route->PlannedRoundTripDistanceCm / 100.0f : 0.0f,
        Route ? Route->CompletedRoundTrips : 0,
        CentralProfileViewportWidget.IsValid() ? TEXT("true") : TEXT("false"));
}

FString AOpenMassCrowdSpawner::GetInvestorDemoEvidenceSnapshot() const
{
    FString StationJson;
    float MaximumRoofErrorCm = 0.0f;
    for (int32 Index = 0; Index < InvestorStations.Num(); ++Index)
    {
        const FInvestorStationRuntime& Station = InvestorStations[Index];
        if (!StationJson.IsEmpty())
        {
            StationJson += TEXT(",");
        }
        MaximumRoofErrorCm = FMath::Max(
            MaximumRoofErrorCm,
            FMath::Max(Station.RoofErrorCm, 0.0f));
        StationJson += FString::Printf(
            TEXT("{\"station_id\":\"%s\",\"roof_validated\":%s,\"validation_attempts\":%d,\"consecutive_validation_misses\":%d,\"station_to_live_roof_offset_cm\":%.3f,\"configured_anchor_lod_adjustment_cm\":%.3f,\"configured_roof\":{\"x\":%.3f,\"y\":%.3f,\"z\":%.3f},\"validated_roof\":{\"x\":%.3f,\"y\":%.3f,\"z\":%.3f},\"coverage_radius_m\":%.1f}"),
            *Station.StationId.ToString(),
            Station.bRoofValidated ? TEXT("true") : TEXT("false"),
            Station.ValidationAttempts,
            Station.ConsecutiveValidationMisses,
            Station.RoofErrorCm,
            Station.ConfiguredAnchorAdjustmentCm,
            Station.ConfiguredRoofPoint.X,
            Station.ConfiguredRoofPoint.Y,
            Station.ConfiguredRoofPoint.Z,
            Station.ValidatedRoofPoint.X,
            Station.ValidatedRoofPoint.Y,
            Station.ValidatedRoofPoint.Z,
            Station.CoverageRadiusCm / 100.0f);
    }

    int32 EnteringCount = 0;
    int32 IndoorCount = 0;
    int32 ExitingCount = 0;
    int32 OutdoorCount = 0;
    for (const FInvestorPersonRuntime& Person : InvestorPeople)
    {
        switch (Person.LocationState)
        {
        case EInvestorPersonLocationState::Entering:
            ++EnteringCount;
            break;
        case EInvestorPersonLocationState::Indoor:
            ++IndoorCount;
            break;
        case EInvestorPersonLocationState::Exiting:
            ++ExitingCount;
            break;
        case EInvestorPersonLocationState::Outdoor:
        default:
            ++OutdoorCount;
            break;
        }
    }

    const int32 ValidStationCount = GetInvestorValidatedStationCount();
    const int32 ConnectedCount = GetInvestorConnectedCount();
    TSet<int32> OccupiedPresentationBands;
    TMap<int32, int32> ActiveLanePopulations;
    int32 ValidatedOffsetCount = 0;
    int32 PresentationFallbackCount = 0;
    if (UMassSpawnerSubsystem* SpawnerSubsystem =
            UWorld::GetSubsystem<UMassSpawnerSubsystem>(GetWorld()))
    {
        FMassEntityManager& EntityManager =
            SpawnerSubsystem->GetEntityManagerChecked();
        for (int32 StableIndex = 0;
             StableIndex < SpawnedEntities.Num();
             ++StableIndex)
        {
            const FMassEntityHandle Entity = SpawnedEntities[StableIndex];
            if (!EntityManager.IsEntityValid(Entity))
            {
                continue;
            }
            const float OffsetCm =
                GetInvestorPresentationOffsetCm(StableIndex);
            const bool bOffsetSupported = FMath::IsNearlyZero(OffsetCm) ||
                (CentralPresentationOffsetValid.IsValidIndex(StableIndex) &&
                 CentralPresentationOffsetValid[StableIndex] != 0);
            if (bOffsetSupported)
            {
                OccupiedPresentationBands.Add(
                    GetInvestorPresentationBandIndex(StableIndex));
                ++ValidatedOffsetCount;
            }
            else
            {
                ++PresentationFallbackCount;
            }
            if (const FMassZoneGraphLaneLocationFragment* Lane =
                    EntityManager.GetFragmentDataPtr<
                        FMassZoneGraphLaneLocationFragment>(Entity))
            {
                ++ActiveLanePopulations.FindOrAdd(
                    Lane->LaneHandle.Index);
            }
        }
    }
    int32 LargestActiveLanePopulation = 0;
    for (const TPair<int32, int32>& Entry : ActiveLanePopulations)
    {
        LargestActiveLanePopulation = FMath::Max(
            LargestActiveLanePopulation,
            Entry.Value);
    }
    float MaximumStationarySeconds = 0.0f;
    for (const float StationarySeconds : CentralTelemetryStationarySeconds)
    {
        MaximumStationarySeconds = FMath::Max(
            MaximumStationarySeconds,
            StationarySeconds);
    }
    const bool bProfileComplete =
        SelectedCentralProfileEntityIndex != INDEX_NONE &&
        CentralProfileViewportWidget.IsValid();
    const int32 ExpectedInvestorPopulation = GetRequestedCentralPopulation();
    const int32 MinimumHealthyMovingPopulation = FMath::CeilToInt(
        static_cast<float>(ExpectedInvestorPopulation) * 0.95f);
    const bool bAssociationVisualEnabled =
        IsValid(InvestorAssociationLineBatch) &&
        InvestorAssociationLineBatch->IsVisible();
    const bool bAssociationFullCoverage =
        bAssociationVisualEnabled &&
        InvestorAssociationVisualRevision > 0 &&
        InvestorAssociationSourceConnectedCount == ConnectedCount &&
        InvestorAssociationRenderedLinkCount == ConnectedCount;
    const bool bPassed =
        bInvestorDeliveryDemoEnabled &&
        InvestorPeople.Num() == ExpectedInvestorPopulation &&
        SpawnedEntities.Num() == ExpectedInvestorPopulation &&
        CentralAdmittedEntityCount == ExpectedInvestorPopulation &&
        CentralExpectedMovingEntityCount == ExpectedInvestorPopulation &&
        CentralMovingEntityCount >= MinimumHealthyMovingPopulation &&
        CentralRepresentedEntityCount == ExpectedInvestorPopulation &&
        CentralStuckEntityCount == 0 &&
        ValidStationCount == 2 &&
        ConnectedCount > 0 &&
        InvestorBuildingEntryCount > 0 &&
        InvestorBuildingExitCount > 0 &&
        InvestorStationReacquisitionCount > 0 &&
        OccupiedPresentationBands.Num() >= 3 &&
        bProfileComplete &&
        bInvestorPersistedSignalLayerReady &&
        InvestorPersistedSignalActorCount ==
            InvestorExpectedSignalActorCount &&
        InvestorPersistedSignalVisibleActorCount ==
            InvestorExpectedSignalActorCount &&
        InvestorPersistedSignalSourceCount ==
            InvestorExpectedSignalSourceCount &&
        InvestorPersistedSignalRayCount == InvestorExpectedSignalRayCount &&
        bAssociationFullCoverage &&
        InvestorLegacyFloatingSignalVisibleCount == 0;
    return FString::Printf(
        TEXT("{\"schema\":\"telecomtwin-investor-delivery-v3\",\"mode_enabled\":%s,\"passed\":%s,\"population\":{\"configured\":%d,\"spawned\":%d,\"admitted\":%d,\"moving\":%d,\"represented\":%d,\"vat_far_walking\":%d},\"liveness\":{\"expected_moving\":%d,\"moving\":%d,\"stuck\":%d,\"maximum_stationary_s\":%.3f,\"stall_recovery_replans\":%d,\"cached_ground_fallbacks\":%d,\"edge_liveness_advances\":%d},\"presentation\":{\"configured_bands\":%d,\"occupied_supported_bands\":%d,\"offset_supported_people\":%d,\"certified_center_fallback_people\":%d,\"maximum_lateral_offset_cm\":%.1f,\"unique_active_lanes\":%d,\"largest_active_lane_population\":%d,\"skeletal_walk_distance_m\":%.1f,\"vat_visible_distance_m\":%.1f,\"high_actor_budget\":%d,\"low_actor_budget\":%d,\"high_actors\":%d,\"low_actors\":%d,\"vat_actors\":%d},\"performance\":{\"ground_guards_per_pass\":%d,\"telemetry_interval_s\":%.2f,\"debug_refresh_hz\":%.1f,\"validated_roof_refresh_s\":%.2f,\"network_refresh_hz\":%.2f,\"frame_samples\":%d,\"frame_p50_ms\":%.3f,\"frame_p95_ms\":%.3f,\"frame_maximum_ms\":%.3f},\"stations\":{\"required\":2,\"validated\":%d,\"maximum_roof_error_cm\":%.3f,\"items\":[%s]},\"network\":{\"connected\":%d,\"uncovered\":%d,\"association_visual_budget\":%d,\"association_visual_enabled\":%s,\"association_visual_policy\":\"all_connected_people_persistent_batch\",\"association_source_connected\":%d,\"association_rendered_links\":%d,\"association_rendered_dashed_links\":%d,\"association_rendered_segments\":%d,\"association_visual_revision\":%d,\"association_full_coverage\":%s,\"rotating_sampling\":false,\"short_lifetime_debug_lines\":false,\"persistent_batch_component\":true,\"persistent_batch_component_tick\":false,\"single_batch_refresh\":true,\"selected_link_style\":\"solid_blue\",\"other_link_style\":\"dashed_gray\",\"station_endpoint_offset_cm\":4.0},\"building\":{\"portal_grounded\":%s,\"portal\":{\"x\":%.3f,\"y\":%.3f,\"z\":%.3f},\"outdoor\":%d,\"entering\":%d,\"indoor\":%d,\"exiting\":%d,\"entry_events\":%d,\"exit_events\":%d,\"station_reacquisitions\":%d},\"profile\":{\"selected_index\":%d,\"visible\":%s,\"anchor\":\"lower_left\",\"required_fields_present\":%s},\"signal_rendering\":{\"source\":\"persisted_editor_actor_components\",\"parity_ready\":%s,\"original_actor_count\":%d,\"original_visible_actor_count\":%d,\"source_actor_count\":%d,\"ray_actor_count\":%d,\"transforms_modified\":false,\"actor_visibility_modified\":false,\"runtime_rebuild_enabled\":false,\"runtime_overlay_enabled\":false},\"legacy_signal\":{\"suppressed_actor_count\":%d,\"restorable\":false,\"preserved_visible\":true,\"runtime_overlay_enabled\":false,\"floating_mock_loaded_count\":%d,\"floating_mock_visible_count\":%d,\"actor_spawn_guard\":true,\"level_stream_guard\":true,\"late_stream_scan_hz\":%.1f},\"video_required\":false}"),
        bInvestorDeliveryDemoEnabled ? TEXT("true") : TEXT("false"),
        bPassed ? TEXT("true") : TEXT("false"),
        InvestorDeliveryPopulation,
        SpawnedEntities.Num(),
        CentralAdmittedEntityCount,
        CentralMovingEntityCount,
        CentralRepresentedEntityCount,
        CentralVATRepresentationCount,
        CentralExpectedMovingEntityCount,
        CentralMovingEntityCount,
        CentralStuckEntityCount,
        MaximumStationarySeconds,
        CentralStallRecoveryReplanCount,
        CentralInvestorCachedGroundFallbackCount,
        CentralEdgeLivenessAdvanceCount,
        InvestorPresentationBandCount,
        OccupiedPresentationBands.Num(),
        ValidatedOffsetCount,
        PresentationFallbackCount,
        static_cast<float>(InvestorPresentationBandCount / 2) *
            InvestorPresentationBandSpacingCm,
        ActiveLanePopulations.Num(),
        LargestActiveLanePopulation,
        InvestorSkeletalWalkDistanceCm / 100.0f,
        InvestorVATVisibleDistanceCm / 100.0f,
        InvestorHighActorBudget,
        InvestorLowActorBudget,
        CentralHighActorRepresentationCount,
        CentralLowActorRepresentationCount,
        CentralVATRepresentationCount,
        InvestorGroundGuardsPerPass,
        CentralTelemetrySampleIntervalSeconds,
        1.0f / InvestorAssociationVisualRefreshSeconds,
        InvestorValidatedRoofRefreshSeconds,
        1.0f / InvestorNetworkRefreshSeconds,
        CentralFrameTimeSampleCount,
        CentralFrameTimeP50Ms,
        CentralFrameTimeP95Ms,
        CentralFrameTimeMaximumMs,
        ValidStationCount,
        MaximumRoofErrorCm,
        *StationJson,
        ConnectedCount,
        FMath::Max(InvestorPeople.Num() - ConnectedCount -
            EnteringCount - IndoorCount - ExitingCount, 0),
        InvestorPeople.Num(),
        bAssociationVisualEnabled ? TEXT("true") : TEXT("false"),
        InvestorAssociationSourceConnectedCount,
        InvestorAssociationRenderedLinkCount,
        InvestorAssociationRenderedDashedLinkCount,
        InvestorAssociationRenderedSegmentCount,
        InvestorAssociationVisualRevision,
        bAssociationFullCoverage ? TEXT("true") : TEXT("false"),
        !InvestorBuildingPortalLocation.IsNearlyZero() ? TEXT("true") : TEXT("false"),
        InvestorBuildingPortalLocation.X,
        InvestorBuildingPortalLocation.Y,
        InvestorBuildingPortalLocation.Z,
        OutdoorCount,
        EnteringCount,
        IndoorCount,
        ExitingCount,
        InvestorBuildingEntryCount,
        InvestorBuildingExitCount,
        InvestorStationReacquisitionCount,
        SelectedCentralProfileEntityIndex,
        CentralProfileViewportWidget.IsValid() ? TEXT("true") : TEXT("false"),
        bProfileComplete ? TEXT("true") : TEXT("false"),
        bInvestorPersistedSignalLayerReady ? TEXT("true") : TEXT("false"),
        InvestorPersistedSignalActorCount,
        InvestorPersistedSignalVisibleActorCount,
        InvestorPersistedSignalSourceCount,
        InvestorPersistedSignalRayCount,
        InvestorLegacyFloatingSignalLoadedCount,
        InvestorLegacyFloatingSignalLoadedCount,
        InvestorLegacyFloatingSignalVisibleCount,
        1.0f / InvestorLegacySignalSuppressionSeconds);
}

FString AOpenMassCrowdSpawner::GetCentralVATAnimationEvidenceSnapshot() const
{
    if (NetworkMode != EOpenMassCrowdNetworkMode::CentralCertifiedCache ||
        SpawnedEntities.IsEmpty() || !GetWorld())
    {
        return TEXT("{\"valid\":false,\"reason\":\"central_population_unavailable\"}");
    }
    UMassSpawnerSubsystem* SpawnerSubsystem =
        UWorld::GetSubsystem<UMassSpawnerSubsystem>(GetWorld());
    const APlayerController* PlayerController =
        UGameplayStatics::GetPlayerController(this, 0);
    if (!SpawnerSubsystem || !PlayerController ||
        !PlayerController->PlayerCameraManager)
    {
        return TEXT("{\"valid\":false,\"reason\":\"camera_or_mass_unavailable\"}");
    }

    FMassEntityManager& EntityManager =
        SpawnerSubsystem->GetEntityManagerChecked();
    const FVector CameraLocation =
        PlayerController->PlayerCameraManager->GetCameraLocation();
    int32 SelectedIndex = INDEX_NONE;
    float SelectedDistanceSquared = -1.0f;
    for (int32 StableIndex = 0;
         StableIndex < SpawnedEntities.Num();
         ++StableIndex)
    {
        const FMassEntityHandle Entity = SpawnedEntities[StableIndex];
        if (!EntityManager.IsEntityValid(Entity))
        {
            continue;
        }
        const FMassRepresentationFragment* Representation =
            EntityManager.GetFragmentDataPtr<FMassRepresentationFragment>(Entity);
        const FTransformFragment* Transform =
            EntityManager.GetFragmentDataPtr<FTransformFragment>(Entity);
        const FOpenMassCrowdVATPlaybackFragment* Playback =
            EntityManager.GetFragmentDataPtr<FOpenMassCrowdVATPlaybackFragment>(Entity);
        const FMassVelocityFragment* Velocity =
            EntityManager.GetFragmentDataPtr<FMassVelocityFragment>(Entity);
        if (!Representation || !Transform || !Playback || !Velocity ||
            Representation->CurrentRepresentation !=
                EMassRepresentationType::StaticMeshInstance)
        {
            continue;
        }
        // A stopped VAT instance is valid runtime state, but it cannot prove
        // that the distant silhouette is walking instead of sliding. Prefer a
        // genuinely moving Mass entity for the visual gait evidence target.
        if (Velocity->Value.SizeSquared2D() < FMath::Square(10.0f) ||
            Playback->PlayRate <= KINDA_SMALL_NUMBER)
        {
            continue;
        }
        const float DistanceSquared = FVector::DistSquared(
            CameraLocation,
            Transform->GetTransform().GetLocation());
        if (DistanceSquared > SelectedDistanceSquared)
        {
            SelectedIndex = StableIndex;
            SelectedDistanceSquared = DistanceSquared;
        }
    }
    if (SelectedIndex == INDEX_NONE)
    {
        return TEXT("{\"valid\":false,\"reason\":\"vat_entity_unavailable\"}");
    }

    return GetCentralVATAnimationEvidenceSnapshotForStableIndex(SelectedIndex);
}

FString AOpenMassCrowdSpawner::GetCentralVATAnimationEvidenceSnapshotForStableIndex(
    const int32 StableEntityIndex) const
{
    if (NetworkMode != EOpenMassCrowdNetworkMode::CentralCertifiedCache ||
        !SpawnedEntities.IsValidIndex(StableEntityIndex) || !GetWorld())
    {
        return TEXT("{\"valid\":false,\"reason\":\"central_entity_unavailable\"}");
    }
    UMassSpawnerSubsystem* SpawnerSubsystem =
        UWorld::GetSubsystem<UMassSpawnerSubsystem>(GetWorld());
    const APlayerController* PlayerController =
        UGameplayStatics::GetPlayerController(this, 0);
    if (!SpawnerSubsystem || !PlayerController ||
        !PlayerController->PlayerCameraManager)
    {
        return TEXT("{\"valid\":false,\"reason\":\"camera_or_mass_unavailable\"}");
    }

    FMassEntityManager& EntityManager =
        SpawnerSubsystem->GetEntityManagerChecked();
    const FMassEntityHandle Entity = SpawnedEntities[StableEntityIndex];
    if (!EntityManager.IsEntityValid(Entity))
    {
        return TEXT("{\"valid\":false,\"reason\":\"entity_invalid\"}");
    }
    const FMassRepresentationFragment* Representation =
        EntityManager.GetFragmentDataPtr<FMassRepresentationFragment>(Entity);
    const FTransformFragment* SelectedTransform =
        EntityManager.GetFragmentDataPtr<FTransformFragment>(Entity);
    const FOpenMassCrowdVATPlaybackFragment* SelectedPlayback =
        EntityManager.GetFragmentDataPtr<FOpenMassCrowdVATPlaybackFragment>(Entity);
    const FMassVelocityFragment* SelectedVelocity =
        EntityManager.GetFragmentDataPtr<FMassVelocityFragment>(Entity);
    if (!Representation || !SelectedTransform || !SelectedPlayback ||
        !SelectedVelocity || Representation->CurrentRepresentation !=
            EMassRepresentationType::StaticMeshInstance)
    {
        return TEXT("{\"valid\":false,\"reason\":\"stable_entity_not_vat\"}");
    }

    const float CurrentFrame =
        UAnimToTextureInstancePlaybackLibrary::GetFrame(
            GetWorld()->GetTimeSeconds(),
            SelectedPlayback->StartFrame,
            SelectedPlayback->EndFrame,
            SelectedPlayback->TimeOffset,
            SelectedPlayback->PlayRate,
            30.0f);
    const bool bAnimationActive =
        SelectedPlayback->PlayRate > KINDA_SMALL_NUMBER &&
        SelectedPlayback->EndFrame > SelectedPlayback->StartFrame;
    const FVector SelectedLocation =
        SelectedTransform->GetTransform().GetLocation();
    const float SelectedDistanceSquared = FVector::DistSquared(
        PlayerController->PlayerCameraManager->GetCameraLocation(),
        SelectedLocation);
    return FString::Printf(
        TEXT("{\"valid\":true,\"stable_index\":%d,\"person_id\":\"%s\",\"representation\":\"VAT\",\"location\":{\"x\":%.3f,\"y\":%.3f,\"z\":%.3f},\"speed_cm_s\":%.3f,\"distance_m\":%.3f,\"animation_active\":%s,\"time_offset\":%.6f,\"play_rate\":%.6f,\"start_frame\":%.3f,\"end_frame\":%.3f,\"current_frame\":%.6f,\"world_time_seconds\":%.6f}"),
        StableEntityIndex,
        *GetCentralPersonId(StableEntityIndex),
        SelectedLocation.X,
        SelectedLocation.Y,
        SelectedLocation.Z,
        SelectedVelocity->Value.Size2D(),
        FMath::Sqrt(SelectedDistanceSquared) / 100.0f,
        bAnimationActive ? TEXT("true") : TEXT("false"),
        SelectedPlayback->TimeOffset,
        SelectedPlayback->PlayRate,
        SelectedPlayback->StartFrame,
        SelectedPlayback->EndFrame,
        CurrentFrame,
        GetWorld()->GetTimeSeconds());
}

FString AOpenMassCrowdSpawner::GetCentralLODEvidenceSnapshot() const
{
    constexpr int32 EvidenceEntityIndex = 0;
    if (NetworkMode != EOpenMassCrowdNetworkMode::CentralCertifiedCache ||
        !SpawnedEntities.IsValidIndex(EvidenceEntityIndex) ||
        !GetWorld())
    {
        return TEXT("{\"valid\":false,\"reason\":\"central_entity_unavailable\"}");
    }

    UMassSpawnerSubsystem* SpawnerSubsystem =
        UWorld::GetSubsystem<UMassSpawnerSubsystem>(GetWorld());
    if (!SpawnerSubsystem)
    {
        return TEXT("{\"valid\":false,\"reason\":\"mass_spawner_subsystem_unavailable\"}");
    }

    FMassEntityManager& EntityManager =
        SpawnerSubsystem->GetEntityManagerChecked();
    const FMassEntityHandle Entity = SpawnedEntities[EvidenceEntityIndex];
    if (!EntityManager.IsEntityValid(Entity))
    {
        return TEXT("{\"valid\":false,\"reason\":\"mass_entity_invalid\"}");
    }

    const FTransformFragment* TransformFragment =
        EntityManager.GetFragmentDataPtr<FTransformFragment>(Entity);
    const FMassRepresentationFragment* RepresentationFragment =
        EntityManager.GetFragmentDataPtr<FMassRepresentationFragment>(Entity);
    const FMassRepresentationLODFragment* LODFragment =
        EntityManager.GetFragmentDataPtr<FMassRepresentationLODFragment>(Entity);
    const FOpenMassCrowdVATPlaybackFragment* PlaybackFragment =
        EntityManager.GetFragmentDataPtr<FOpenMassCrowdVATPlaybackFragment>(Entity);
    const FMassActorFragment* ActorFragment =
        EntityManager.GetFragmentDataPtr<FMassActorFragment>(Entity);
    if (!TransformFragment || !RepresentationFragment || !LODFragment)
    {
        return TEXT("{\"valid\":false,\"reason\":\"evidence_fragments_unavailable\"}");
    }

    const auto RepresentationName = [](const EMassRepresentationType Type)
    {
        switch (Type)
        {
        case EMassRepresentationType::HighResSpawnedActor:
            return TEXT("HighActor");
        case EMassRepresentationType::LowResSpawnedActor:
            return TEXT("LowActor");
        case EMassRepresentationType::StaticMeshInstance:
            return TEXT("VAT");
        case EMassRepresentationType::None:
            return TEXT("None");
        default:
            return TEXT("Unknown");
        }
    };
    const auto LODName = [](const EMassLOD::Type LOD)
    {
        switch (LOD)
        {
        case EMassLOD::High:
            return TEXT("High");
        case EMassLOD::Medium:
            return TEXT("Medium");
        case EMassLOD::Low:
            return TEXT("Low");
        case EMassLOD::Off:
            return TEXT("Off");
        default:
            return TEXT("Unknown");
        }
    };

    const FVector Location = TransformFragment->GetTransform().GetLocation();
    const int32 AppearanceSeed =
        Entity.Index * 196613 + Entity.SerialNumber * 314159;
    const int32 VariantIndex =
        CentralEntityVisualVariantIndices.IsValidIndex(EvidenceEntityIndex)
            ? CentralEntityVisualVariantIndices[EvidenceEntityIndex]
            : INDEX_NONE;
    const FString VariantName =
        CentralRuntimeVariantNames.IsValidIndex(VariantIndex)
            ? CentralRuntimeVariantNames[VariantIndex].ToString()
            : TEXT("Unavailable");

    FString ActorPath = TEXT("None");
    FString ActorClass = TEXT("None");
    FString ActorTier = TEXT("None");
    int32 ActorAppearanceSeed = INDEX_NONE;
    bool bActorHidden = true;
    const AActor* VisualActor =
        ActorFragment && ActorFragment->IsOwnedByMass()
            ? ActorFragment->Get()
            : nullptr;
    if (IsValid(VisualActor))
    {
        ActorPath = VisualActor->GetPathName();
        ActorClass = VisualActor->GetClass()->GetPathName();
        bActorHidden = VisualActor->IsHidden();
        if (const AOpenMassCrowdCitySampleActor* CitySampleActor =
            Cast<AOpenMassCrowdCitySampleActor>(VisualActor))
        {
            ActorTier = CitySampleActor->GetRepresentationTier().ToString();
            ActorAppearanceSeed = CitySampleActor->GetMassAppearanceSeed();
        }
    }

    float VATTimeOffset = 0.0f;
    float VATPlayRate = 0.0f;
    float VATStartFrame = 0.0f;
    float VATEndFrame = 0.0f;
    float VATCurrentFrame = 0.0f;
    if (PlaybackFragment)
    {
        VATTimeOffset = PlaybackFragment->TimeOffset;
        VATPlayRate = PlaybackFragment->PlayRate;
        VATStartFrame = PlaybackFragment->StartFrame;
        VATEndFrame = PlaybackFragment->EndFrame;
        VATCurrentFrame = UAnimToTextureInstancePlaybackLibrary::GetFrame(
            GetWorld()->GetTimeSeconds(),
            VATStartFrame,
            VATEndFrame,
            VATTimeOffset,
            VATPlayRate,
            30.0f);
    }

    return FString::Printf(
        TEXT("{\"valid\":true,\"stable_entity_slot\":%d,\"mass_entity_index\":%d,\"mass_entity_serial\":%d,\"appearance_seed\":%d,\"variant_index\":%d,\"variant_name\":\"%s\",\"representation\":\"%s\",\"previous_representation\":\"%s\",\"lod\":\"%s\",\"location\":{\"x\":%.6f,\"y\":%.6f,\"z\":%.6f},\"actor_path\":\"%s\",\"actor_class\":\"%s\",\"actor_tier\":\"%s\",\"actor_appearance_seed\":%d,\"actor_hidden\":%s,\"vat_time_offset\":%.6f,\"vat_play_rate\":%.6f,\"vat_start_frame\":%.6f,\"vat_end_frame\":%.6f,\"vat_current_frame\":%.6f,\"world_time_seconds\":%.6f}"),
        EvidenceEntityIndex,
        Entity.Index,
        Entity.SerialNumber,
        AppearanceSeed,
        VariantIndex,
        *VariantName,
        RepresentationName(RepresentationFragment->CurrentRepresentation),
        RepresentationName(RepresentationFragment->PrevRepresentation),
        LODName(LODFragment->LOD),
        Location.X,
        Location.Y,
        Location.Z,
        *ActorPath,
        *ActorClass,
        *ActorTier,
        ActorAppearanceSeed,
        bActorHidden ? TEXT("true") : TEXT("false"),
        VATTimeOffset,
        VATPlayRate,
        VATStartFrame,
        VATEndFrame,
        VATCurrentFrame,
        GetWorld()->GetTimeSeconds());
}

void AOpenMassCrowdSpawner::DestroyRuntimePopulation()
{
    GetWorldTimerManager().ClearTimer(CentralAdmissionTimer);
    HideCentralProfile();
    HideInvestorKPI();
    ClearInvestorAssociationVisuals();
    InvestorStations.Reset();
    InvestorPeople.Reset();
    bInvestorDemoInitialized = false;
    bInvestorAutoProfileOpened = false;
    bCentralProfileInputConfigured = false;
    bCentralProfileHasPreviousControlRotation = false;
    if (UMassSpawnerSubsystem* SpawnerSubsystem =
        UWorld::GetSubsystem<UMassSpawnerSubsystem>(GetWorld()))
    {
        if (!SpawnedEntities.IsEmpty())
        {
            SpawnerSubsystem->DestroyEntities(SpawnedEntities);
        }
    }
    SpawnedEntities.Reset();
    CentralEntityVisualVariantIndices.Reset();
    CentralPresentationOffsetValid.Reset();
    CentralRuntimeVariantNames.Reset();
    LastValidGroundStates.Reset();
    RuntimeCentralPreviousFrameStates.Reset();
    RuntimeCentralPreviousFrameShortPaths.Reset();
    RuntimeCentralPreviousFrameMoveTargets.Reset();
    RuntimeCentralPreviousFrameNavigationValid.Reset();
    RuntimeCentralYieldAnchorStates.Reset();
    RuntimeCentralYieldAnchorShortPaths.Reset();
    RuntimeCentralYieldAnchorMoveTargets.Reset();
    RuntimeCentralYieldAnchorNavigationValid.Reset();

    if (IsValid(RuntimeZoneGraphData))
    {
        RuntimeZoneGraphData->Destroy();
    }
    RuntimeZoneGraphData = nullptr;
    RuntimeLaneHandles.Reset();
    RuntimeCentralLaneLengthsCm.Reset();
    RuntimeCentralLaneIds.Reset();
    RuntimeCentralLaneCellIds.Reset();
    RuntimeCentralLaneComponentIds.Reset();
    RuntimeCentralLaneFromNodeIds.Reset();
    RuntimeCentralLaneToNodeIds.Reset();
    RuntimeCentralPhysicalTrackIndices.Reset();
    RuntimeCentralSameDirectionPhysicalLaneIndices.Reset();
    RuntimeCentralOpposingPhysicalLaneIndices.Reset();
    RuntimeCentralLocalConflicts.Reset();
    RuntimeCentralLocalConflictClusterIndices.Reset();
    RuntimeCentralLocalConflictIndicesByCluster.Reset();
    RuntimeCentralLocalConflictClusterCount = 0;
    RuntimeCentralLocalConflictIndicesByLane.Reset();
    RuntimeCentralLocalConflictClosuresByLane.Reset();
    RuntimeCentralCollisionConflictLaneIndices.Reset();
    RuntimeCentralReverseLaneIndices.Reset();
    RuntimeCentralCrossingFlags.Reset();
    RuntimeCentralLaneIndicesById.Reset();
    RuntimeUnavailableCentralCellIds.Reset();
    RuntimeKnownCentralCellIds.Reset();
    RuntimeCentralCellRecoveryGuardCounts.Reset();
    RuntimeCentralCellFailedGuardEntities.Reset();
    RuntimeCentralDistricts.Reset();
    CentralSpawnLanePlan.Reset();
    CentralSpawnDistancePlan.Reset();
    CentralSpawnPositionPlan.Reset();
    CentralSpawnDistrictPlan.Reset();
    CentralReserveSpawnSlotsByPlanIndex.Reset();
    CentralReserveSpawnCursorsByPlanIndex.Reset();
    CentralSpawnLivePositionPlan.Reset();
    CentralSpawnLivePositionValid.Reset();
    CentralRuntimeTemplateIds.Reset();
    CentralRuntimeVariantRemainingCounts.Reset();
    EntityRouteStates.Reset();
    RuntimeCentralCorridorWaitSeconds.Reset();
    RuntimeCentralCorridorWaitLaneIndices.Reset();
    RuntimeCentralLocalConflictWaitSeconds.Reset();
    RuntimeCentralLocalConflictWaitResourceIndices.Reset();
    PendingCentralConflictReplans.Reset();
    RuntimeTraits.Reset();
    RuntimeNetworkNodeCount = 0;
    RouteAssignmentCount = 0;
    CompletedTripCount = 0;
    CentralShortPathChunkCount = 0;
    RouteReplanCount = 0;
    GroundProjectionFailureCount = 0;
    GroundRollbackCount = 0;
    GroundCenterRecoveryCount = 0;
    GroundUnrecoverableCount = 0;
    CurrentUnsupportedVisualCount = 0;
    MaxConsecutiveGroundMisses = 0;
    CentralGroundGuardQueryCount = 0;
    CentralGroundCandidateComponentTestCount = 0;
    CentralGroundComponentCacheRefreshCount = 0;
    CurrentHighResRepresentationCount = 0;
    CurrentLowResRepresentationCount = 0;
    CentralAdmissionTargetCount = 0;
    CentralAdmittedEntityCount = 0;
    bCentralAdmissionReleased = false;
    CentralSimulatedEntityCount = 0;
    CentralRepresentedEntityCount = 0;
    CentralAdmissionBatchCount = 0;
    CentralMaximumCommittedAdmissionBatchSize = 0;
    CentralReserveSpawnSlotCount = 0;
    CentralAdmissionLiveGroundProbeCount = 0;
    CentralAdmissionLiveGroundRejectCount = 0;
    CentralAdmissionNoRawSupportCount = 0;
    CentralReserveReplacementCount = 0;
    CentralReserveCycleDeferCount = 0;
    CentralPlannedSpawnSlotCount = 0;
    CentralMinimumPlannedSpawnClearanceCm = -1.0f;
    ResetCentralRuntimeTelemetry();
    CentralAvailabilityRevision = 0;
    CentralGroundGuardBucketCursor = 0;
    CentralWaitingRouteRetryCursor = 0;
    LastLoggedCentralRepresentedCount = INDEX_NONE;
    CesiumGroundComponentGrid.Reset();
    CesiumGroundLargeComponents.Reset();
    CesiumGroundComponentCacheRefreshWorldTime = -1.0;
    bCesiumGroundComponentCacheInitialized = false;
}
