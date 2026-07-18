#pragma once

#include "CoreMinimal.h"
#include "GameFramework/Actor.h"
#include "MassEntityHandle.h"
#include "OpenMassCrowdVisualization.h"
#include "ZoneGraphTypes.h"

#include "OpenMassCrowdSpawner.generated.h"

class AZoneGraphData;
class UMassEntityTraitBase;
class USceneComponent;

/**
 * Builds a branching runtime pedestrian ZoneGraph from Cesium-grounded points,
 * assigns multi-lane A* trips, and renders the moving MassCrowd population.
 */
UCLASS(BlueprintType)
class OPENMASSCROWD_API AOpenMassCrowdSpawner final : public AActor
{
    GENERATED_BODY()

public:
    AOpenMassCrowdSpawner();

    virtual void Tick(float DeltaSeconds) override;

    UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "Open Mass Crowd")
    TObjectPtr<USceneComponent> SceneRoot;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Open Mass Crowd", meta = (ClampMin = "1", ClampMax = "500"))
    int32 PopulationCount = 30;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Open Mass Crowd")
    FVector2D RouteHalfExtent = FVector2D(720.0, 250.0);

    /** Maximum XY distance between collision-grounded samples on each lane. */
    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Open Mass Crowd", meta = (ClampMin = "50.0", ClampMax = "500.0"))
    float NetworkGroundSampleSpacing = 150.0f;

    /** Pedestrian corridor width; runtime generation safely clamps legacy values to 120..160 cm. */
    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Open Mass Crowd", meta = (ClampMin = "120.0", ClampMax = "160.0"))
    float LaneWidth = 120.0f;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Open Mass Crowd", meta = (ClampMin = "5.0", ClampMax = "300.0"))
    float GroundTolerance = 90.0f;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Open Mass Crowd", meta = (ClampMin = "100.0"))
    float TraceHeight = 1600.0f;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Open Mass Crowd", meta = (ClampMin = "100.0"))
    float TraceDepth = 2600.0f;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Open Mass Crowd", meta = (ClampMin = "0.1", ClampMax = "2.0"))
    float GroundCorrectionInterval = 0.2f;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Open Mass Crowd")
    bool bSpawnOnBeginPlay = true;

    /**
     * Temporary engine mannequin by default. Replace these soft references with
     * migrated City Sample VAT variants without touching movement code.
     */
    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Open Mass Crowd|Visual")
    TArray<FOpenMassCrowdVisualConfig> VisualVariants;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Open Mass Crowd|Visual", meta = (ClampMin = "0.0"))
    float VATTimeOffsetSpread = 3.0f;

    UFUNCTION(BlueprintCallable, Category = "Open Mass Crowd")
    void SpawnMassPopulation();

    /** Replace temporary mannequin VAT visuals with Epic City Sample actors. */
    UFUNCTION(BlueprintCallable, Category = "Open Mass Crowd|Visual")
    void ConfigureOfficialCitySampleVisual();

    UFUNCTION(BlueprintPure, Category = "Open Mass Crowd")
    int32 GetSpawnedEntityCount() const { return SpawnedEntities.Num(); }

    UFUNCTION(BlueprintPure, Category = "Open Mass Crowd|Navigation")
    int32 GetRuntimeLaneCount() const { return RuntimeLaneHandles.Num(); }

    UFUNCTION(BlueprintPure, Category = "Open Mass Crowd|Navigation")
    int32 GetRuntimeNetworkNodeCount() const { return RuntimeNetworkNodeCount; }

    UFUNCTION(BlueprintPure, Category = "Open Mass Crowd|Navigation")
    int32 GetRouteAssignmentCount() const { return RouteAssignmentCount; }

    UFUNCTION(BlueprintPure, Category = "Open Mass Crowd|Navigation")
    int32 GetCompletedTripCount() const { return CompletedTripCount; }

    UFUNCTION(BlueprintPure, Category = "Open Mass Crowd|Navigation")
    int32 GetRouteReplanCount() const { return RouteReplanCount; }

    UFUNCTION(BlueprintPure, Category = "Open Mass Crowd|Navigation")
    int32 GetGroundProjectionFailureCount() const { return GroundProjectionFailureCount; }

    UFUNCTION(BlueprintPure, Category = "Open Mass Crowd|Navigation")
    int32 GetGroundRollbackCount() const { return GroundRollbackCount; }

    UFUNCTION(BlueprintPure, Category = "Open Mass Crowd|Navigation")
    int32 GetGroundCenterRecoveryCount() const { return GroundCenterRecoveryCount; }

    UFUNCTION(BlueprintPure, Category = "Open Mass Crowd|Navigation")
    int32 GetGroundUnrecoverableCount() const { return GroundUnrecoverableCount; }

    UFUNCTION(BlueprintPure, Category = "Open Mass Crowd|Navigation")
    int32 GetCurrentUnsupportedVisualCount() const { return CurrentUnsupportedVisualCount; }

    UFUNCTION(BlueprintPure, Category = "Open Mass Crowd|Visual")
    int32 GetCurrentHighResRepresentationCount() const { return CurrentHighResRepresentationCount; }

    UFUNCTION(BlueprintPure, Category = "Open Mass Crowd|Visual")
    int32 GetCurrentLowResRepresentationCount() const { return CurrentLowResRepresentationCount; }

protected:
    virtual void BeginPlay() override;
    virtual void EndPlay(const EEndPlayReason::Type EndPlayReason) override;

private:
    struct FEntityRouteState
    {
        TArray<FZoneGraphLaneHandle> LanePath;
        int32 CurrentPathIndex = INDEX_NONE;
        float DestinationDistance = 0.0f;
        int32 CompletedTrips = 0;

        void Reset()
        {
            LanePath.Reset();
            CurrentPathIndex = INDEX_NONE;
            DestinationDistance = 0.0f;
        }
    };

    struct FLastValidGroundState
    {
        FTransform Transform = FTransform::Identity;
        FZoneGraphLaneHandle LaneHandle;
        float DistanceAlongLane = 0.0f;
        float LaneLength = 0.0f;
        int32 ConsecutiveMisses = 0;
        bool bValid = false;
    };

    bool ProjectToCesiumGround(const FVector& XYPoint, FVector& OutGroundPoint) const;
    bool HasPedestrianClearance(const FVector& StartGroundedPoint, const FVector& EndGroundedPoint) const;
    bool HasPedestrianCorridorSupport(const FVector& StartGroundedPoint, const FVector& EndGroundedPoint) const;
    bool BuildGroundedRoute(TArray<FVector>& OutRoutePoints) const;
    bool BuildRuntimeZoneGraph(const TArray<FVector>& GroundedRoute);
    bool SpawnEntitiesOnLanes();
    bool PlanNewDestination(int32 EntityIndex);
    bool RequestNextPath(int32 EntityIndex);
    void RefreshCompletedPaths();
    void CorrectMassGrounding();
    void SyncVisualActorsToMass();
    void RetrySpawn();
    void DestroyRuntimePopulation();

    UPROPERTY(Transient)
    TObjectPtr<AZoneGraphData> RuntimeZoneGraphData;

    UPROPERTY(Transient)
    TArray<TObjectPtr<UMassEntityTraitBase>> RuntimeTraits;

    TArray<FMassEntityHandle> SpawnedEntities;
    TArray<FZoneGraphLaneHandle> RuntimeLaneHandles;
    TArray<FEntityRouteState> EntityRouteStates;
    /** Last exact-XY Cesium-supported Mass and lane state for each stable entity index. */
    TArray<FLastValidGroundState> LastValidGroundStates;
    FRandomStream RouteRandomStream;
    FTimerHandle SpawnRetryTimer;
    int32 RuntimeNetworkNodeCount = 0;
    int32 RouteAssignmentCount = 0;
    int32 CompletedTripCount = 0;
    int32 RouteReplanCount = 0;
    int32 GroundProjectionFailureCount = 0;
    int32 GroundRollbackCount = 0;
    int32 GroundCenterRecoveryCount = 0;
    int32 GroundUnrecoverableCount = 0;
    int32 CurrentUnsupportedVisualCount = 0;
    int32 MaxConsecutiveGroundMisses = 0;
    int32 CurrentHighResRepresentationCount = 0;
    int32 CurrentLowResRepresentationCount = 0;
    int32 GroundRetryCount = 0;
    float PathRefreshAccumulator = 0.0f;
    float GroundCorrectionAccumulator = 0.0f;
};
