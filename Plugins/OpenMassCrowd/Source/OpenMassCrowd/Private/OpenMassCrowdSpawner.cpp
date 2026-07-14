#include "OpenMassCrowdSpawner.h"

#include "OpenMassCrowdTrait.h"
#include "OpenMassCrowdVisualActor.h"

#include "Avoidance/MassAvoidanceTrait.h"
#include "Avoidance/MassNavigationObstacleTrait.h"
#include "Components/PrimitiveComponent.h"
#include "Components/SceneComponent.h"
#include "Engine/World.h"
#include "MassCommonFragments.h"
#include "MassCrowdFragments.h"
#include "MassCrowdMemberTrait.h"
#include "MassCrowdSubsystem.h"
#include "MassEntityConfigAsset.h"
#include "MassEntityManager.h"
#include "MassMovementFragments.h"
#include "MassNavigationFragments.h"
#include "MassSpawnerSubsystem.h"
#include "MassZoneGraphNavigationFragments.h"
#include "MassZoneGraphNavigationTrait.h"
#include "MassZoneGraphNavigationTypes.h"
#include "MassZoneGraphNavigationUtils.h"
#include "Movement/MassMovementTrait.h"
#include "SmoothOrientation/MassSmoothOrientationTrait.h"
#include "Steering/MassSteeringTrait.h"
#include "TimerManager.h"
#include "UObject/UObjectIterator.h"
#include "ZoneGraphData.h"
#include "ZoneGraphQuery.h"
#include "ZoneGraphSubsystem.h"

namespace
{
constexpr float VisualRootHeight = 90.0f;
constexpr int32 MaxGroundRetries = 60;

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
    const int32 LaneIndex)
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
    Storage.LaneLinks.Add(FZoneLaneLinkData(
        LaneIndex,
        EZoneLaneLinkType::Outgoing,
        EZoneLaneLinkFlags::None));
    Lane.LinksEnd = Storage.LaneLinks.Num();
    Lane.ZoneIndex = ZoneIndex;
    Storage.Lanes.Add(Lane);
}
}

AOpenMassCrowdSpawner::AOpenMassCrowdSpawner()
{
    PrimaryActorTick.bCanEverTick = true;
    PrimaryActorTick.TickGroup = TG_PostPhysics;

    SceneRoot = CreateDefaultSubobject<USceneComponent>(TEXT("Root"));
    SetRootComponent(SceneRoot);

    Tags.Add(TEXT("HK_OpenMass_Crowd_Demo"));
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

    SynchronizeVisualActors();

    PathRefreshAccumulator += DeltaSeconds;
    if (PathRefreshAccumulator >= 0.15f)
    {
        PathRefreshAccumulator = 0.0f;
        RefreshCompletedPaths();
    }

    GroundCorrectionAccumulator += DeltaSeconds;
    if (GroundCorrectionAccumulator >= GroundCorrectionInterval)
    {
        GroundCorrectionAccumulator = 0.0f;
        CorrectMassGrounding();
    }
}

bool AOpenMassCrowdSpawner::ProjectToCesiumGround(
    const FVector& XYPoint,
    FVector& OutGroundPoint) const
{
    if (!GetWorld())
    {
        return false;
    }

    const FVector Start(XYPoint.X, XYPoint.Y, GetActorLocation().Z + TraceHeight);
    const FVector End(XYPoint.X, XYPoint.Y, GetActorLocation().Z - TraceDepth);
    FCollisionQueryParams Params(SCENE_QUERY_STAT(OpenMassCrowdGround), true, this);
    Params.AddIgnoredActor(this);

    const auto IsAcceptedCesiumHit = [this](const FHitResult& Hit)
    {
        const UPrimitiveComponent* Component = Hit.GetComponent();
        const bool bCesiumComponent = Component &&
            Component->GetClass()->GetName().Contains(TEXT("CesiumGltfPrimitiveComponent"));
        const bool bWalkableSlope = Hit.ImpactNormal.Z >= 0.72f;
        const bool bExpectedElevation =
            FMath::Abs(Hit.ImpactPoint.Z - GetActorLocation().Z) <= GroundTolerance;

        return bCesiumComponent && bWalkableSlope && bExpectedElevation;
    };

    TArray<FHitResult> Hits;
    if (GetWorld()->LineTraceMultiByChannel(Hits, Start, End, ECC_Visibility, Params))
    {
        for (const FHitResult& Hit : Hits)
        {
            if (IsAcceptedCesiumHit(Hit))
            {
                OutGroundPoint = Hit.ImpactPoint;
                return true;
            }
        }
    }

    // A world trace can be stopped by an unrelated blocking component before it
    // reaches a streamed Cesium tile. Mirror the validated editor setup by
    // querying the loaded Cesium collision components directly as a fallback.
    bool bFoundCesiumHit = false;
    float NearestDistanceSquared = TNumericLimits<float>::Max();
    FHitResult NearestHit;
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
        if (Component->LineTraceComponent(ComponentHit, Start, End, Params) &&
            IsAcceptedCesiumHit(ComponentHit))
        {
            const float DistanceSquared = FVector::DistSquared(Start, ComponentHit.ImpactPoint);
            if (DistanceSquared < NearestDistanceSquared)
            {
                NearestDistanceSquared = DistanceSquared;
                NearestHit = ComponentHit;
                bFoundCesiumHit = true;
            }
        }
    }

    if (bFoundCesiumHit)
    {
        OutGroundPoint = NearestHit.ImpactPoint;
        return true;
    }

    return false;
}

bool AOpenMassCrowdSpawner::BuildGroundedRoute(TArray<FVector>& OutRoutePoints) const
{
    const float X = RouteHalfExtent.X;
    const float Y = RouteHalfExtent.Y;
    const TArray<FVector2D> RelativePoints = {
        FVector2D(-X, -Y), FVector2D(-X * 0.5f, -Y), FVector2D(0.0f, -Y),
        FVector2D(X * 0.5f, -Y), FVector2D(X, -Y), FVector2D(X, 0.0f),
        FVector2D(X, Y), FVector2D(X * 0.5f, Y), FVector2D(0.0f, Y),
        FVector2D(-X * 0.5f, Y), FVector2D(-X, Y), FVector2D(-X, 0.0f)
    };

    OutRoutePoints.Reset(RelativePoints.Num() + 1);
    for (const FVector2D& RelativePoint : RelativePoints)
    {
        FVector GroundPoint;
        const FVector Candidate = GetActorLocation() + FVector(RelativePoint.X, RelativePoint.Y, 0.0f);
        if (!ProjectToCesiumGround(Candidate, GroundPoint))
        {
            OutRoutePoints.Reset();
            return false;
        }
        OutRoutePoints.Add(GroundPoint + FVector(0.0, 0.0, 2.0));
    }

    const FVector FirstRoutePoint = OutRoutePoints[0];
    OutRoutePoints.Add(FirstRoutePoint);
    return true;
}

bool AOpenMassCrowdSpawner::BuildRuntimeZoneGraph(const TArray<FVector>& GroundedRoute)
{
    UZoneGraphSubsystem* ZoneGraphSubsystem =
        UWorld::GetSubsystem<UZoneGraphSubsystem>(GetWorld());
    if (!ZoneGraphSubsystem || GroundedRoute.Num() < 4)
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

    FZoneGraphStorage& Storage = RuntimeZoneGraphData->GetStorageMutable();
    Storage.Reset();

    AppendLane(Storage, GroundedRoute, LaneWidth, 0, 0);

    TArray<FVector> ReverseRoute;
    ReverseRoute.Reserve(GroundedRoute.Num());
    for (int32 PointIndex = GroundedRoute.Num() - 1; PointIndex >= 0; --PointIndex)
    {
        ReverseRoute.Add(GroundedRoute[PointIndex]);
    }
    AppendLane(Storage, ReverseRoute, LaneWidth, 0, 1);

    FBox RouteBounds(ForceInit);
    for (const FVector& Point : GroundedRoute)
    {
        RouteBounds += Point;
    }
    RouteBounds = RouteBounds.ExpandBy(FVector(LaneWidth * 0.5f, LaneWidth * 0.5f, 100.0f));

    FZoneData Zone;
    Zone.LanesBegin = 0;
    Zone.LanesEnd = Storage.Lanes.Num();
    Zone.Bounds = RouteBounds;
    Zone.Tags = FZoneGraphTagMask(1);
    Storage.Zones.Add(Zone);
    Storage.Bounds = RouteBounds;

    ZoneGraphSubsystem->RegisterZoneGraphData(*RuntimeZoneGraphData);
    const FZoneGraphDataHandle DataHandle = RuntimeZoneGraphData->GetStorage().DataHandle;
    if (!DataHandle.IsValid())
    {
        return false;
    }

    RuntimeLaneHandles = {
        FZoneGraphLaneHandle(0, DataHandle),
        FZoneGraphLaneHandle(1, DataHandle)
    };
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

    GroundRetryCount = 0;
    if (!BuildRuntimeZoneGraph(GroundedRoute) || !SpawnEntitiesOnLanes())
    {
        UE_LOG(LogTemp, Error, TEXT("OPEN_MASS_CROWD_ABORT runtime Mass/ZoneGraph setup failed"));
        DestroyRuntimePopulation();
        return;
    }

    UE_LOG(
        LogTemp,
        Warning,
        TEXT("OPEN_MASS_CROWD_READY requested=%d spawned=%d lanes=%d cesium_route_points=%d center=(%.2f,%.2f,%.2f)"),
        PopulationCount,
        SpawnedEntities.Num(),
        RuntimeLaneHandles.Num(),
        GroundedRoute.Num() - 1,
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
    if (!SpawnerSubsystem || !ZoneGraphSubsystem || !CrowdSubsystem || RuntimeLaneHandles.Num() != 2)
    {
        return false;
    }

    RuntimeTraits.Reset();
    FMassEntityConfig EntityConfig(*this);
    AddRuntimeTrait<UOpenMassCrowdTrait>(*this, EntityConfig, RuntimeTraits);
    AddRuntimeTrait<UMassMovementTrait>(*this, EntityConfig, RuntimeTraits);
    AddRuntimeTrait<UMassSteeringTrait>(*this, EntityConfig, RuntimeTraits);
    AddRuntimeTrait<UMassSmoothOrientationTrait>(*this, EntityConfig, RuntimeTraits);
    AddRuntimeTrait<UMassNavigationObstacleTrait>(*this, EntityConfig, RuntimeTraits);
    AddRuntimeTrait<UMassObstacleAvoidanceTrait>(*this, EntityConfig, RuntimeTraits);
    AddRuntimeTrait<UMassZoneGraphNavigationTrait>(*this, EntityConfig, RuntimeTraits);
    AddRuntimeTrait<UMassCrowdMemberTrait>(*this, EntityConfig, RuntimeTraits);

    if (!EntityConfig.ValidateEntityTemplate(*GetWorld()))
    {
        UE_LOG(LogTemp, Error, TEXT("OPEN_MASS_CROWD_TEMPLATE_INVALID"));
        return false;
    }

    const FMassEntityTemplate& EntityTemplate =
        EntityConfig.GetOrCreateEntityTemplate(*GetWorld());
    TSharedPtr<FMassEntityManager::FEntityCreationContext> CreationContext =
        SpawnerSubsystem->SpawnEntities(EntityTemplate, PopulationCount, SpawnedEntities);
    if (!CreationContext.IsValid() || SpawnedEntities.Num() != PopulationCount)
    {
        return false;
    }

    // Finish Mass observers before assigning our runtime lane. The default
    // ZoneGraph location initializer uses spatial lookup, while this demo owns
    // exact lane handles and distances for its generated route.
    CreationContext.Reset();

    FMassEntityManager& EntityManager = SpawnerSubsystem->GetEntityManagerChecked();
    VisualActors.Reset(PopulationCount);

    for (int32 EntityIndex = 0; EntityIndex < SpawnedEntities.Num(); ++EntityIndex)
    {
        const FMassEntityHandle Entity = SpawnedEntities[EntityIndex];
        const FZoneGraphLaneHandle LaneHandle = RuntimeLaneHandles[EntityIndex % RuntimeLaneHandles.Num()];
        float LaneLength = 0.0f;
        if (!ZoneGraphSubsystem->GetLaneLength(LaneHandle, LaneLength) || LaneLength <= 1.0f)
        {
            return false;
        }

        const int32 LanePopulation = FMath::CeilToInt(PopulationCount * 0.5f);
        const int32 IndexOnLane = EntityIndex / RuntimeLaneHandles.Num();
        const float DistanceAlongLane =
            FMath::Fmod((IndexOnLane + 0.35f * (EntityIndex % 3)) * LaneLength / LanePopulation, LaneLength);

        FZoneGraphLaneLocation SpawnLocation;
        if (!ZoneGraphSubsystem->CalculateLocationAlongLane(
            LaneHandle,
            DistanceAlongLane,
            SpawnLocation))
        {
            return false;
        }

        FTransform InitialTransform(SpawnLocation.Tangent.ToOrientationQuat(), SpawnLocation.Position);
        EntityManager.GetFragmentDataChecked<FTransformFragment>(Entity).SetTransform(InitialTransform);
        EntityManager.GetFragmentDataChecked<FAgentRadiusFragment>(Entity).Radius = 30.0f;

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

        FActorSpawnParameters SpawnParameters;
        SpawnParameters.Owner = this;
        SpawnParameters.SpawnCollisionHandlingOverride =
            ESpawnActorCollisionHandlingMethod::AlwaysSpawn;
        AOpenMassCrowdVisualActor* VisualActor =
            GetWorld()->SpawnActor<AOpenMassCrowdVisualActor>(
                AOpenMassCrowdVisualActor::StaticClass(),
                InitialTransform.GetLocation() + FVector(0.0, 0.0, VisualRootHeight),
                InitialTransform.Rotator(),
                SpawnParameters);
        VisualActors.Add(VisualActor);
    }

    for (int32 EntityIndex = 0; EntityIndex < SpawnedEntities.Num(); ++EntityIndex)
    {
        if (!RequestNextPath(EntityIndex))
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

bool AOpenMassCrowdSpawner::RequestNextPath(const int32 EntityIndex)
{
    UMassSpawnerSubsystem* SpawnerSubsystem =
        UWorld::GetSubsystem<UMassSpawnerSubsystem>(GetWorld());
    UZoneGraphSubsystem* ZoneGraphSubsystem =
        UWorld::GetSubsystem<UZoneGraphSubsystem>(GetWorld());
    if (!SpawnerSubsystem || !ZoneGraphSubsystem || !SpawnedEntities.IsValidIndex(EntityIndex))
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

    FZoneGraphShortPathRequest PathRequest;
    PathRequest.StartPosition =
        EntityManager.GetFragmentDataChecked<FTransformFragment>(Entity).GetTransform().GetLocation();
    PathRequest.TargetDistance = LaneLocation.LaneLength;
    PathRequest.NextLaneHandle = LaneLocation.LaneHandle;
    PathRequest.NextExitLinkType = EZoneLaneLinkType::Outgoing;
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
        if (ShortPath.IsDone())
        {
            RequestNextPath(EntityIndex);
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

    FMassEntityManager& EntityManager = SpawnerSubsystem->GetEntityManagerChecked();
    int32 CorrectedCount = 0;
    for (const FMassEntityHandle Entity : SpawnedEntities)
    {
        if (!EntityManager.IsEntityValid(Entity))
        {
            continue;
        }

        FTransformFragment& TransformFragment =
            EntityManager.GetFragmentDataChecked<FTransformFragment>(Entity);
        FTransform& Transform = TransformFragment.GetMutableTransform();
        const FVector CurrentLocation = Transform.GetLocation();
        FVector GroundPoint;
        if (ProjectToCesiumGround(CurrentLocation, GroundPoint))
        {
            Transform.SetLocation(FVector(CurrentLocation.X, CurrentLocation.Y, GroundPoint.Z + 2.0f));
            ++CorrectedCount;
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

void AOpenMassCrowdSpawner::SynchronizeVisualActors()
{
    UMassSpawnerSubsystem* SpawnerSubsystem =
        UWorld::GetSubsystem<UMassSpawnerSubsystem>(GetWorld());
    if (!SpawnerSubsystem)
    {
        return;
    }

    FMassEntityManager& EntityManager = SpawnerSubsystem->GetEntityManagerChecked();
    const int32 Count = FMath::Min(SpawnedEntities.Num(), VisualActors.Num());
    for (int32 EntityIndex = 0; EntityIndex < Count; ++EntityIndex)
    {
        const FMassEntityHandle Entity = SpawnedEntities[EntityIndex];
        AOpenMassCrowdVisualActor* VisualActor = VisualActors[EntityIndex];
        if (!EntityManager.IsEntityValid(Entity) || !IsValid(VisualActor))
        {
            continue;
        }

        const FTransform& MassTransform =
            EntityManager.GetFragmentDataChecked<FTransformFragment>(Entity).GetTransform();
        const FVector VisualLocation =
            MassTransform.GetLocation() + FVector(0.0, 0.0, VisualRootHeight);
        VisualActor->SetActorLocationAndRotation(
            VisualLocation,
            MassTransform.GetRotation(),
            false,
            nullptr,
            ETeleportType::TeleportPhysics);
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

    for (AOpenMassCrowdVisualActor* VisualActor : VisualActors)
    {
        if (IsValid(VisualActor))
        {
            VisualActor->Destroy();
        }
    }
    VisualActors.Reset();

    if (IsValid(RuntimeZoneGraphData))
    {
        RuntimeZoneGraphData->Destroy();
    }
    RuntimeZoneGraphData = nullptr;
    RuntimeLaneHandles.Reset();
    RuntimeTraits.Reset();
}
