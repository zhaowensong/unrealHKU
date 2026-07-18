#pragma once

#include "CoreMinimal.h"
#include "GameFramework/Actor.h"

#include "OpenMassCrowdCitySampleActor.generated.h"

class UAnimSequence;
class UChildActorComponent;
class USceneComponent;
class USkeletalMeshComponent;

/**
 * Lightweight Mass representation actor that hosts Epic's official
 * BP_CrowdCharacter and drives its modular meshes with a walking animation.
 *
 * All marketplace references are soft so the project and plugin still load on
 * machines where the UE-Only Fab content has not been mounted yet.
 */
UCLASS(BlueprintType)
class OPENMASSCROWD_API AOpenMassCrowdCitySampleActor : public AActor
{
    GENERATED_BODY()

public:
    AOpenMassCrowdCitySampleActor();

    UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "Open Mass Crowd|City Sample")
    TObjectPtr<USceneComponent> SceneRoot;

    UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "Open Mass Crowd|City Sample")
    TObjectPtr<UChildActorComponent> CitySampleCharacter;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Open Mass Crowd|City Sample")
    TSoftClassPtr<AActor> CitySampleCharacterClass;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Open Mass Crowd|City Sample")
    TSoftObjectPtr<UAnimSequence> WalkAnimation;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Open Mass Crowd|City Sample")
    FName DrivingMeshComponentName = TEXT("SkeletalMeshComponent0");

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Open Mass Crowd|City Sample", meta = (ClampMin = "0.1", ClampMax = "3.0"))
    float WalkPlayRate = 1.0f;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Open Mass Crowd|City Sample", meta = (ClampMin = "0.0", ClampMax = "0.25"))
    float WalkPlayRateVariation = 0.08f;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Open Mass Crowd|City Sample")
    float GroundOffset = 0.0f;

    /**
     * City Sample's MTN walk clips advance along local +Y, while Mass movement
     * uses Unreal's conventional local +X forward axis.
     */
    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Open Mass Crowd|City Sample")
    float CharacterYawOffsetDegrees = -90.0f;

    /** Zero lets the City Sample skeletal meshes select their normal render LOD. */
    UPROPERTY(EditDefaultsOnly, BlueprintReadOnly, Category = "Open Mass Crowd|City Sample|LOD", meta = (ClampMin = "0", ClampMax = "8"))
    int32 ForcedSkeletalLOD = 0;

    UPROPERTY(EditDefaultsOnly, BlueprintReadOnly, Category = "Open Mass Crowd|City Sample|LOD")
    bool bLowCostRepresentation = false;

    UPROPERTY(EditDefaultsOnly, BlueprintReadOnly, Category = "Open Mass Crowd|City Sample|LOD", meta = (ClampMin = "0.0", ClampMax = "0.5"))
    float LowCostAnimationTickInterval = 0.066f;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Open Mass Crowd|City Sample|Animation", meta = (ClampMin = "1.0"))
    float ReferenceWalkSpeedCmPerSecond = 135.0f;

    /** Zero pauses the walk clip when Mass is stopped instead of moonwalking in place. */
    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Open Mass Crowd|City Sample|Animation", meta = (ClampMin = "0.0", ClampMax = "3.0"))
    float MinimumWalkPlayRate = 0.0f;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Open Mass Crowd|City Sample|Animation", meta = (ClampMin = "0.1", ClampMax = "3.0"))
    float MaximumWalkPlayRate = 1.35f;

    /** Called by the Mass owner after movement; this actor never owns navigation. */
    UFUNCTION(BlueprintCallable, Category = "Open Mass Crowd|City Sample")
    void SetMassSpeedCmPerSecond(float SpeedCmPerSecond);

    /** Keeps one Mass pedestrian's official randomized appearance stable across LOD actors. */
    UFUNCTION(BlueprintCallable, Category = "Open Mass Crowd|City Sample")
    void SetMassAppearanceSeed(int32 AppearanceSeed);

    /** Stable Mass identity used to correlate this pedestrian across representation swaps. */
    UFUNCTION(BlueprintPure, Category = "Open Mass Crowd|City Sample")
    int32 GetMassAppearanceSeed() const { return AppliedAppearanceSeed; }

    UFUNCTION(BlueprintPure, Category = "Open Mass Crowd|City Sample")
    FName GetRepresentationTier() const;

protected:
    virtual void BeginPlay() override;

private:
    bool InitializeOfficialCharacter(int32 AppearanceSeed);

    UPROPERTY(Transient)
    TObjectPtr<USkeletalMeshComponent> DrivingMeshComponent;

    float IndividualPlayRateScale = 1.0f;
    int32 AppliedAppearanceSeed = INDEX_NONE;
    int32 FailedAppearanceSeed = INDEX_NONE;
    double NextAppearanceRetryTimeSeconds = 0.0;
};

/**
 * Medium/low Mass representation. It still uses official City Sample content,
 * but forces cheaper skeletal LODs and removes costly distance-only details.
 */
UCLASS(BlueprintType)
class OPENMASSCROWD_API AOpenMassCrowdCitySampleLowResActor final
    : public AOpenMassCrowdCitySampleActor
{
    GENERATED_BODY()

public:
    AOpenMassCrowdCitySampleLowResActor();
};
