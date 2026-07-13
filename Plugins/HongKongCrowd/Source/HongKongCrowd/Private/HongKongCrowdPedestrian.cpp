#include "HongKongCrowdPedestrian.h"

#include "Animation/AnimationAsset.h"
#include "Components/CapsuleComponent.h"
#include "Components/SkeletalMeshComponent.h"
#include "DetourCrowdAIController.h"
#include "Engine/SkeletalMesh.h"
#include "GameFramework/CharacterMovementComponent.h"
#include "UObject/UObjectGlobals.h"

AHongKongCrowdPedestrian::AHongKongCrowdPedestrian()
{
    PrimaryActorTick.bCanEverTick = false;

    AIControllerClass = ADetourCrowdAIController::StaticClass();
    AutoPossessAI = EAutoPossessAI::PlacedInWorldOrSpawned;

    GetCapsuleComponent()->InitCapsuleSize(30.0f, 90.0f);
    GetCapsuleComponent()->SetCollisionResponseToChannel(ECC_Pawn, ECR_Block);

    bUseControllerRotationYaw = false;
    UCharacterMovementComponent* Movement = GetCharacterMovement();
    Movement->bOrientRotationToMovement = true;
    Movement->RotationRate = FRotator(0.0, 420.0, 0.0);
    Movement->MaxWalkSpeed = 185.0f;
    Movement->BrakingDecelerationWalking = 420.0f;

    if (USkeletalMesh* LoadedMesh = LoadObject<USkeletalMesh>(
        nullptr, TEXT("/Game/BattleWizardPolyart/Meshes/WizardSM.WizardSM")))
    {
        GetMesh()->SetSkeletalMesh(LoadedMesh);
        GetMesh()->SetRelativeLocation(FVector(0.0, 0.0, -90.0));
        GetMesh()->SetRelativeRotation(FRotator(0.0, -90.0, 0.0));
        GetMesh()->SetCollisionEnabled(ECollisionEnabled::NoCollision);
    }

    if (UAnimationAsset* Animation = LoadObject<UAnimationAsset>(
        nullptr, TEXT("/Game/BattleWizardPolyart/Animations/WalkForwardAnim.WalkForwardAnim")))
    {
        WalkAnimation = Animation;
    }
}

void AHongKongCrowdPedestrian::BeginPlay()
{
    Super::BeginPlay();

    if (WalkAnimation && GetMesh())
    {
        GetMesh()->SetAnimationMode(EAnimationMode::AnimationSingleNode);
        GetMesh()->PlayAnimation(WalkAnimation, true);
    }
}
