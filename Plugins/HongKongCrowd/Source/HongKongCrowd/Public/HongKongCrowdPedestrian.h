#pragma once

#include "CoreMinimal.h"
#include "GameFramework/Character.h"

#include "HongKongCrowdPedestrian.generated.h"

class UAnimationAsset;

UCLASS(BlueprintType)
class HONGKONGCROWD_API AHongKongCrowdPedestrian : public ACharacter
{
    GENERATED_BODY()

public:
    AHongKongCrowdPedestrian();

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Hong Kong Crowd")
    TObjectPtr<UAnimationAsset> WalkAnimation;

protected:
    virtual void BeginPlay() override;
};
