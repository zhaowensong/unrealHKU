#pragma once

#include "CoreMinimal.h"
#include "GameFramework/Actor.h"

#include "HongKongCrowdSpawner.generated.h"

class AHongKongCrowdPedestrian;
class UStaticMeshComponent;

UCLASS(BlueprintType)
class HONGKONGCROWD_API AHongKongCrowdSpawner : public AActor
{
    GENERATED_BODY()

public:
    AHongKongCrowdSpawner();

    virtual void OnConstruction(const FTransform& Transform) override;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Hong Kong Crowd", meta = (ClampMin = "1", ClampMax = "200"))
    int32 PopulationCount = 12;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Hong Kong Crowd")
    FVector2D AreaHalfExtent = FVector2D(550.0, 350.0);

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Hong Kong Crowd", meta = (ClampMin = "5.0", ClampMax = "250.0"))
    float GroundTolerance = 80.0f;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Hong Kong Crowd", meta = (ClampMin = "100.0"))
    float TraceHeight = 1400.0f;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Hong Kong Crowd", meta = (ClampMin = "100.0"))
    float TraceDepth = 2600.0f;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Hong Kong Crowd")
    bool bSpawnOnBeginPlay = true;

    UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "Hong Kong Crowd")
    TObjectPtr<UStaticMeshComponent> WalkableProxy;

    UFUNCTION(BlueprintCallable, Category = "Hong Kong Crowd")
    void SpawnPopulation();

    UFUNCTION(BlueprintPure, Category = "Hong Kong Crowd")
    TArray<AHongKongCrowdPedestrian*> GetPedestrians() const;

protected:
    virtual void BeginPlay() override;

private:
    bool ValidateCesiumGround(const FVector& XYPoint, FVector& OutGroundPoint) const;
    bool FindValidPoint(int32 Seed, FVector& OutPoint) const;
    void UpdateProxySize();
    void UpdateCrowd();
    void AssignDestination(AHongKongCrowdPedestrian* Pedestrian, int32 Seed);

    UPROPERTY(Transient)
    TArray<TObjectPtr<AHongKongCrowdPedestrian>> SpawnedPedestrians;

    UPROPERTY(Transient)
    TMap<TObjectPtr<AHongKongCrowdPedestrian>, FVector> Destinations;

    FTimerHandle SpawnTimerHandle;
    FTimerHandle UpdateTimerHandle;
    int32 DestinationSequence = 0;
    int32 GroundRetryCount = 0;
};
