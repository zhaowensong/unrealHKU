#pragma once

#include "CoreMinimal.h"
#include "GameFramework/Actor.h"
#include "MassEntityHandle.h"
#include "ZoneGraphTypes.h"

#include "OpenMassCrowdSpawner.generated.h"

class AOpenMassCrowdVisualActor;
class AZoneGraphData;
class UMassEntityTraitBase;
class USceneComponent;

/**
 * Builds a two-way runtime ZoneGraph loop from Cesium-grounded route points,
 * spawns genuine Mass crowd entities, and keeps a lightweight visual actor in
 * sync with each entity for this 30-person demo.
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

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Open Mass Crowd", meta = (ClampMin = "120.0", ClampMax = "800.0"))
    float LaneWidth = 360.0f;

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

    UFUNCTION(BlueprintCallable, Category = "Open Mass Crowd")
    void SpawnMassPopulation();

    UFUNCTION(BlueprintPure, Category = "Open Mass Crowd")
    int32 GetSpawnedEntityCount() const { return SpawnedEntities.Num(); }

protected:
    virtual void BeginPlay() override;
    virtual void EndPlay(const EEndPlayReason::Type EndPlayReason) override;

private:
    bool ProjectToCesiumGround(const FVector& XYPoint, FVector& OutGroundPoint) const;
    bool BuildGroundedRoute(TArray<FVector>& OutRoutePoints) const;
    bool BuildRuntimeZoneGraph(const TArray<FVector>& GroundedRoute);
    bool SpawnEntitiesOnLanes();
    bool RequestNextPath(int32 EntityIndex);
    void RefreshCompletedPaths();
    void CorrectMassGrounding();
    void SynchronizeVisualActors();
    void RetrySpawn();
    void DestroyRuntimePopulation();

    UPROPERTY(Transient)
    TObjectPtr<AZoneGraphData> RuntimeZoneGraphData;

    UPROPERTY(Transient)
    TArray<TObjectPtr<AOpenMassCrowdVisualActor>> VisualActors;

    UPROPERTY(Transient)
    TArray<TObjectPtr<UMassEntityTraitBase>> RuntimeTraits;

    TArray<FMassEntityHandle> SpawnedEntities;
    TArray<FZoneGraphLaneHandle> RuntimeLaneHandles;
    FTimerHandle SpawnRetryTimer;
    int32 GroundRetryCount = 0;
    float PathRefreshAccumulator = 0.0f;
    float GroundCorrectionAccumulator = 0.0f;
};
