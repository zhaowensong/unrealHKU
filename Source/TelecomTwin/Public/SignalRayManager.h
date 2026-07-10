#pragma once

#include "CoreMinimal.h"
#include "GameFramework/Actor.h"
#include "SignalSimulationTypes.h"
#include "SignalRayManager.generated.h"

class UHierarchicalInstancedStaticMeshComponent;
class UMaterialInterface;
class USceneComponent;

UENUM(BlueprintType)
enum class ESignalStrengthBand : uint8
{
    High,
    Medium,
    Low
};

USTRUCT(BlueprintType)
struct TELECOMTWIN_API FSignalRaySegmentRecord
{
    GENERATED_BODY()

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Signal Ray")
    FVector Start = FVector::ZeroVector;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Signal Ray")
    FVector End = FVector::ZeroVector;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Signal Ray", meta = (ClampMin = "0.0", ClampMax = "1.0"))
    float NormalizedStrength = 1.0f;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Signal Ray", meta = (ClampMin = "0"))
    int32 BounceIndex = 0;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Signal Ray")
    FName SourceId = NAME_None;
};

/**
 * The single persistent owner of all signal-ray visualization instances.
 *
 * The actor is non-spatially-loaded so World Partition keeps exactly one
 * manager available. Rays and reflection nodes are batched in HISM components;
 * individual ray actors are never spawned.
 */
UCLASS(BlueprintType, Blueprintable)
class TELECOMTWIN_API ASignalRayManager : public AActor
{
    GENERATED_BODY()

public:
    ASignalRayManager();

    UFUNCTION(BlueprintCallable, Category = "Signal Ray")
    void ClearVisualization();

    UFUNCTION(BlueprintCallable, Category = "Signal Ray")
    int32 AddRaySegment(
        FVector Start,
        FVector End,
        float NormalizedStrength,
        int32 BounceIndex,
        FName SourceId);

    UFUNCTION(BlueprintCallable, Category = "Signal Ray")
    int32 AddReflectionNode(FVector Position, float NormalizedStrength, int32 BounceIndex);

    UFUNCTION(BlueprintCallable, Category = "Signal Ray|Collision")
    void ClearCollisionProxies();

    UFUNCTION(BlueprintCallable, Category = "Signal Ray|Collision")
    int32 AddCollisionProxy(FVector Center, FVector Extent, FRotator Rotation, FName ProxyId);

    UFUNCTION(BlueprintCallable, Category = "Signal Ray")
    void RebuildVisualization(const TArray<FSignalRaySegmentRecord>& Segments);

    UFUNCTION(BlueprintPure, Category = "Signal Ray")
    int32 GetRaySegmentCount() const;

    UFUNCTION(BlueprintPure, Category = "Signal Ray")
    int32 GetReflectionNodeCount() const;

    UFUNCTION(BlueprintPure, Category = "Signal Ray|Collision")
    int32 GetCollisionProxyCount() const;

    UFUNCTION(BlueprintCallable, Category = "Signal Ray|Simulation")
    void ApplySimulationFrame(const FSignalSimulationFrame& Frame);

    UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "Signal Ray|Components")
    TObjectPtr<USceneComponent> SceneRoot;

    UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "Signal Ray|Components")
    TObjectPtr<UHierarchicalInstancedStaticMeshComponent> HighStrengthRays;

    UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "Signal Ray|Components")
    TObjectPtr<UHierarchicalInstancedStaticMeshComponent> MediumStrengthRays;

    UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "Signal Ray|Components")
    TObjectPtr<UHierarchicalInstancedStaticMeshComponent> LowStrengthRays;

    UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "Signal Ray|Components")
    TObjectPtr<UHierarchicalInstancedStaticMeshComponent> ReflectionNodes;

    UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "Signal Ray|Collision")
    TObjectPtr<UHierarchicalInstancedStaticMeshComponent> BuildingCollisionProxies;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Signal Ray|Rendering", meta = (ClampMin = "0.01"))
    float MinimumRayRadius = 0.18f;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Signal Ray|Rendering", meta = (ClampMin = "0.01"))
    float MaximumRayRadius = 0.42f;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Signal Ray|Rendering", meta = (ClampMin = "0.01"))
    float MinimumNodeScale = 0.05f;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Signal Ray|Rendering", meta = (ClampMin = "0.01"))
    float MaximumNodeScale = 0.14f;

private:
    ESignalStrengthBand GetStrengthBand(float NormalizedStrength) const;
    UHierarchicalInstancedStaticMeshComponent* GetRayComponent(ESignalStrengthBand Band) const;
    static FTransform MakeSegmentTransform(FVector Start, FVector End, float Radius);
    static void ConfigureHISM(UHierarchicalInstancedStaticMeshComponent* Component);
    static void SetInstanceMetadata(
        UHierarchicalInstancedStaticMeshComponent* Component,
        int32 InstanceIndex,
        float NormalizedStrength,
        int32 BounceIndex);
};
