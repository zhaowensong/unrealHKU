#pragma once

#include "CoreMinimal.h"
#include "GameFramework/Actor.h"

#include "OpenMassCrowdCitySampleActor.generated.h"

class UAnimSequence;
class UChildActorComponent;
class USceneComponent;

/**
 * Lightweight Mass representation actor that hosts Epic's official
 * BP_CrowdCharacter and drives its modular meshes with a walking animation.
 *
 * All marketplace references are soft so the project and plugin still load on
 * machines where the UE-Only Fab content has not been mounted yet.
 */
UCLASS(BlueprintType)
class OPENMASSCROWD_API AOpenMassCrowdCitySampleActor final : public AActor
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

protected:
    virtual void BeginPlay() override;

private:
    bool InitializeOfficialCharacter();
};
