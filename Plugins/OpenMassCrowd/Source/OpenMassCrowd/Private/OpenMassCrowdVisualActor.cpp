#include "OpenMassCrowdVisualActor.h"

#include "Animation/AnimationAsset.h"
#include "Components/SceneComponent.h"
#include "Components/SkeletalMeshComponent.h"
#include "Engine/SkeletalMesh.h"
#include "UObject/UObjectGlobals.h"

AOpenMassCrowdVisualActor::AOpenMassCrowdVisualActor()
{
    PrimaryActorTick.bCanEverTick = false;
    SetActorEnableCollision(false);

    USceneComponent* Root = CreateDefaultSubobject<USceneComponent>(TEXT("Root"));
    SetRootComponent(Root);

    Mesh = CreateDefaultSubobject<USkeletalMeshComponent>(TEXT("PedestrianMesh"));
    Mesh->SetupAttachment(Root);
    Mesh->SetCollisionEnabled(ECollisionEnabled::NoCollision);
    Mesh->SetGenerateOverlapEvents(false);
    Mesh->SetRelativeLocation(FVector(0.0, 0.0, -90.0));
    Mesh->SetRelativeRotation(FRotator(0.0, -90.0, 0.0));

    if (USkeletalMesh* LoadedMesh = LoadObject<USkeletalMesh>(
        nullptr, TEXT("/Game/BattleWizardPolyart/Meshes/WizardSM.WizardSM")))
    {
        Mesh->SetSkeletalMesh(LoadedMesh);
    }

    WalkAnimation = LoadObject<UAnimationAsset>(
        nullptr,
        TEXT("/Game/BattleWizardPolyart/Animations/WalkForwardAnim.WalkForwardAnim"));

    Tags.Add(TEXT("HK_OpenMass_Crowd_Visual"));
}

void AOpenMassCrowdVisualActor::BeginPlay()
{
    Super::BeginPlay();

    if (Mesh && WalkAnimation)
    {
        Mesh->SetAnimationMode(EAnimationMode::AnimationSingleNode);
        Mesh->PlayAnimation(WalkAnimation, true);
    }
}
