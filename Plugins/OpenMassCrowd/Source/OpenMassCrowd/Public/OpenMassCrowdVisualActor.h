#pragma once

#include "CoreMinimal.h"
#include "GameFramework/Actor.h"

#include "OpenMassCrowdVisualActor.generated.h"

class UAnimationAsset;
class USkeletalMeshComponent;

/** Lightweight visual only. Movement and avoidance remain owned by the Mass entity. */
UCLASS(NotPlaceable)
class OPENMASSCROWD_API AOpenMassCrowdVisualActor final : public AActor
{
    GENERATED_BODY()

public:
    AOpenMassCrowdVisualActor();

    UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "Open Mass Crowd")
    TObjectPtr<USkeletalMeshComponent> Mesh;

    UPROPERTY(Transient)
    TObjectPtr<UAnimationAsset> WalkAnimation;

protected:
    virtual void BeginPlay() override;
};
