#include "OpenMassCrowdCitySampleActor.h"

#include "Animation/AnimSequence.h"
#include "Components/ChildActorComponent.h"
#include "Components/PrimitiveComponent.h"
#include "Components/SceneComponent.h"
#include "Components/SkeletalMeshComponent.h"

AOpenMassCrowdCitySampleActor::AOpenMassCrowdCitySampleActor()
{
    PrimaryActorTick.bCanEverTick = false;

    SceneRoot = CreateDefaultSubobject<USceneComponent>(TEXT("Root"));
    SetRootComponent(SceneRoot);

    CitySampleCharacter = CreateDefaultSubobject<UChildActorComponent>(TEXT("OfficialCitySampleCharacter"));
    CitySampleCharacter->SetupAttachment(SceneRoot);

    CitySampleCharacterClass = TSoftClassPtr<AActor>(FSoftObjectPath(
        TEXT("/Game/CitySampleCrowd/Blueprints/BP_CrowdCharacter.BP_CrowdCharacter_C")));
    WalkAnimation = TSoftObjectPtr<UAnimSequence>(FSoftObjectPath(
        TEXT("/Game/CitySampleCrowd/Character/Anims/Loco/MTN_Set/MTN_N_Walk_F_VarB.MTN_N_Walk_F_VarB")));

    Tags.Add(TEXT("HK_OpenMass_CitySample_Visual"));
}

void AOpenMassCrowdCitySampleActor::BeginPlay()
{
    Super::BeginPlay();

    if (!InitializeOfficialCharacter())
    {
        SetActorHiddenInGame(true);
    }
}

bool AOpenMassCrowdCitySampleActor::InitializeOfficialCharacter()
{
    UClass* LoadedCharacterClass = CitySampleCharacterClass.LoadSynchronous();
    UAnimSequence* LoadedWalkAnimation = WalkAnimation.LoadSynchronous();
    if (!LoadedCharacterClass || !LoadedCharacterClass->IsChildOf(AActor::StaticClass()))
    {
        UE_LOG(
            LogTemp,
            Error,
            TEXT("OPEN_MASS_CITY_SAMPLE_MISSING character=%s"),
            *CitySampleCharacterClass.ToSoftObjectPath().ToString());
        return false;
    }

    CitySampleCharacter->SetRelativeLocation(FVector(0.0, 0.0, GroundOffset));
    CitySampleCharacter->SetRelativeRotation(FRotator(0.0, CharacterYawOffsetDegrees, 0.0));
    CitySampleCharacter->SetChildActorClass(LoadedCharacterClass);

    // SetChildActorClass creates the registered component's default child
    // immediately. Recreate it with a pre-construction customizer so Epic's
    // own SetRandomOptions function runs before BP_CrowdCharacter's
    // Construction Script selects its compatible modular meshes.
    CitySampleCharacter->DestroyChildActor();
    bool bRandomizedAppearance = false;
    CitySampleCharacter->CreateChildActor(
        [&bRandomizedAppearance](AActor* DeferredChildActor)
        {
            UFunction* SetRandomOptionsFunction =
                DeferredChildActor
                    ? DeferredChildActor->FindFunction(TEXT("SetRandomOptions"))
                    : nullptr;
            if (!SetRandomOptionsFunction)
            {
                UE_LOG(
                    LogTemp,
                    Error,
                    TEXT("OPEN_MASS_CITY_SAMPLE_RANDOM_OPTIONS_MISSING"));
                return;
            }
            if (SetRandomOptionsFunction->NumParms != 0)
            {
                UE_LOG(
                    LogTemp,
                    Error,
                    TEXT("OPEN_MASS_CITY_SAMPLE_RANDOM_OPTIONS_SIGNATURE_INVALID parms=%d size=%d"),
                    SetRandomOptionsFunction->NumParms,
                    SetRandomOptionsFunction->ParmsSize);
                return;
            }

            DeferredChildActor->ProcessEvent(SetRandomOptionsFunction, nullptr);
            bRandomizedAppearance = true;
        });

    AActor* ChildActor = CitySampleCharacter->GetChildActor();
    if (!ChildActor)
    {
        UE_LOG(LogTemp, Error, TEXT("OPEN_MASS_CITY_SAMPLE_CHILD_SPAWN_FAILED"));
        return false;
    }

    // The Mass entity owns movement and avoidance. Marketplace character
    // collision must not intercept Cesium ground traces or telecom ray traces.
    ChildActor->SetActorEnableCollision(false);
    TArray<UPrimitiveComponent*> PrimitiveComponents;
    ChildActor->GetComponents<UPrimitiveComponent>(PrimitiveComponents);
    for (UPrimitiveComponent* Component : PrimitiveComponents)
    {
        Component->SetCollisionEnabled(ECollisionEnabled::NoCollision);
        Component->SetGenerateOverlapEvents(false);
    }

    TArray<USkeletalMeshComponent*> SkeletalComponents;
    ChildActor->GetComponents<USkeletalMeshComponent>(SkeletalComponents);
    USkeletalMeshComponent* DrivingMesh = nullptr;
    for (USkeletalMeshComponent* Component : SkeletalComponents)
    {
        if (Component && Component->GetFName() == DrivingMeshComponentName)
        {
            DrivingMesh = Component;
            break;
        }
    }
    if (!DrivingMesh && !SkeletalComponents.IsEmpty())
    {
        DrivingMesh = SkeletalComponents[0];
    }

    if (!DrivingMesh || !LoadedWalkAnimation)
    {
        UE_LOG(
            LogTemp,
            Error,
            TEXT("OPEN_MASS_CITY_SAMPLE_ANIMATION_FAILED child=%s mesh=%s animation=%s"),
            *GetNameSafe(ChildActor),
            *GetNameSafe(DrivingMesh),
            *GetNameSafe(LoadedWalkAnimation));
        return false;
    }

    DrivingMesh->PlayAnimation(LoadedWalkAnimation, true);
    FRandomStream VisualRandom(GetUniqueID());
    DrivingMesh->SetPosition(
        VisualRandom.FRandRange(0.0f, LoadedWalkAnimation->GetPlayLength()),
        false);
    DrivingMesh->SetPlayRate(
        WalkPlayRate * VisualRandom.FRandRange(
            1.0f - WalkPlayRateVariation,
            1.0f + WalkPlayRateVariation));

    static bool bLoggedFirstReadyActor = false;
    if (!bLoggedFirstReadyActor)
    {
        bLoggedFirstReadyActor = true;
        UE_LOG(
            LogTemp,
            Warning,
            TEXT("OPEN_MASS_CITY_SAMPLE_ACTOR_READY child=%s skeletal_components=%d driving_mesh=%s animation=%s randomized=%s"),
            *GetNameSafe(ChildActor),
            SkeletalComponents.Num(),
            *GetNameSafe(DrivingMesh),
            *GetNameSafe(LoadedWalkAnimation),
            bRandomizedAppearance ? TEXT("true") : TEXT("false"));
    }

    return true;
}
