#include "OpenMassCrowdCitySampleActor.h"

#include "Animation/AnimSequence.h"
#include "Components/ActorComponent.h"
#include "Components/ChildActorComponent.h"
#include "Components/PrimitiveComponent.h"
#include "Components/SceneComponent.h"
#include "Components/SkeletalMeshComponent.h"
#include "UObject/UnrealType.h"

namespace
{
struct FOpenMassCrowdAppearanceSnapshot
{
    TMap<FName, FString> ReplayableBlueprintProperties;
};

// Mass may replace a High actor with a Low actor (and later recreate High).
// Cache the official BP options selected for each stable entity seed so both
// representation actors replay the same modular appearance without reseeding
// Unreal's process-global Rand()/FRand() stream.
TMap<FString, FOpenMassCrowdAppearanceSnapshot> GAppearanceSnapshots;

FString MakeAppearanceSnapshotKey(const AActor& Actor, const int32 AppearanceSeed)
{
    return FString::Printf(
        TEXT("%s::%d"),
        *Actor.GetClass()->GetPathName(),
        AppearanceSeed);
}

bool IsReplayableAppearanceProperty(const FProperty& Property)
{
    const UClass* OwnerClass = Property.GetOwnerClass();
    if (!OwnerClass || !OwnerClass->ClassGeneratedBy ||
        !Property.HasAnyPropertyFlags(CPF_Edit | CPF_BlueprintVisible) ||
        Property.HasAnyPropertyFlags(
            CPF_Transient |
            CPF_DuplicateTransient |
            CPF_NonPIEDuplicateTransient |
            CPF_EditorOnly |
            CPF_InstancedReference |
            CPF_ContainsInstancedReference) ||
        Property.IsA<FDelegateProperty>() ||
        Property.IsA<FMulticastDelegateProperty>())
    {
        return false;
    }

    if (const FObjectPropertyBase* ObjectProperty =
            CastField<FObjectPropertyBase>(&Property))
    {
        const UClass* PropertyClass = ObjectProperty->PropertyClass;
        if (PropertyClass &&
            (PropertyClass->IsChildOf(AActor::StaticClass()) ||
             PropertyClass->IsChildOf(UActorComponent::StaticClass())))
        {
            return false;
        }
    }

    return true;
}

TMap<FName, FString> ExportReplayableAppearanceProperties(AActor& Actor)
{
    TMap<FName, FString> Values;
    for (TFieldIterator<FProperty> It(
             Actor.GetClass(),
             EFieldIteratorFlags::IncludeSuper);
         It;
         ++It)
    {
        const FProperty& Property = **It;
        if (!IsReplayableAppearanceProperty(Property))
        {
            continue;
        }

        FString ExportedValue;
        const void* ValueAddress = Property.ContainerPtrToValuePtr<void>(&Actor);
        Property.ExportTextItem_Direct(
            ExportedValue,
            ValueAddress,
            nullptr,
            &Actor,
            PPF_None);
        Values.Add(Property.GetFName(), MoveTemp(ExportedValue));
    }
    return Values;
}

bool ApplyAppearanceSnapshot(
    AActor& Actor,
    const FOpenMassCrowdAppearanceSnapshot& Snapshot)
{
    if (Snapshot.ReplayableBlueprintProperties.IsEmpty())
    {
        return false;
    }

    int32 AppliedCount = 0;
    for (const TPair<FName, FString>& Entry : Snapshot.ReplayableBlueprintProperties)
    {
        FProperty* Property = FindFProperty<FProperty>(Actor.GetClass(), Entry.Key);
        if (!Property || !IsReplayableAppearanceProperty(*Property))
        {
            return false;
        }

        void* ValueAddress = Property->ContainerPtrToValuePtr<void>(&Actor);
        if (!Property->ImportText_Direct(
                *Entry.Value,
                ValueAddress,
                &Actor,
                PPF_None))
        {
            return false;
        }
        ++AppliedCount;
    }

    return AppliedCount == Snapshot.ReplayableBlueprintProperties.Num();
}

bool RandomizeOrReplayOfficialAppearance(
    AActor& Actor,
    UFunction& SetRandomOptionsFunction,
    const int32 AppearanceSeed)
{
    const FString SnapshotKey = MakeAppearanceSnapshotKey(Actor, AppearanceSeed);
    if (const FOpenMassCrowdAppearanceSnapshot* Snapshot =
            GAppearanceSnapshots.Find(SnapshotKey))
    {
        if (ApplyAppearanceSnapshot(Actor, *Snapshot))
        {
            return true;
        }
        GAppearanceSnapshots.Remove(SnapshotKey);
    }

    // Let the official BP choose once using its normal random source. We do not
    // call FMath::RandInit: resetting the global stream here would perturb every
    // other runtime system whenever Mass changes representation LOD.
    Actor.ProcessEvent(&SetRandomOptionsFunction, nullptr);
    const TMap<FName, FString> AfterValues =
        ExportReplayableAppearanceProperties(Actor);

    FOpenMassCrowdAppearanceSnapshot NewSnapshot;
    // Store the complete safe BP option state, not only values that differ from
    // defaults. A legitimately randomized all-default result is still a valid
    // appearance and must remain stable after an LOD actor swap.
    NewSnapshot.ReplayableBlueprintProperties = AfterValues;

    if (NewSnapshot.ReplayableBlueprintProperties.IsEmpty())
    {
        UE_LOG(
            LogTemp,
            Error,
            TEXT("OPEN_MASS_CITY_SAMPLE_APPEARANCE_SNAPSHOT_EMPTY seed=%d"),
            AppearanceSeed);
        return false;
    }

    GAppearanceSnapshots.Add(SnapshotKey, MoveTemp(NewSnapshot));
    return true;
}
}

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

AOpenMassCrowdCitySampleLowResActor::AOpenMassCrowdCitySampleLowResActor()
{
    bLowCostRepresentation = true;
    ForcedSkeletalLOD = 4;
    Tags.Add(TEXT("HK_OpenMass_CitySample_LowRes"));
}

void AOpenMassCrowdCitySampleActor::BeginPlay()
{
    Super::BeginPlay();
    // The Mass entity supplies a stable seed on the owner's next PostUpdate
    // tick. Do not build a throwaway GetUniqueID() appearance first.
    SetActorHiddenInGame(true);
}

bool AOpenMassCrowdCitySampleActor::InitializeOfficialCharacter(const int32 AppearanceSeed)
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
        [&bRandomizedAppearance, AppearanceSeed](AActor* DeferredChildActor)
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

            bRandomizedAppearance = RandomizeOrReplayOfficialAppearance(
                *DeferredChildActor,
                *SetRandomOptionsFunction,
                AppearanceSeed);
        });

    AActor* ChildActor = CitySampleCharacter->GetChildActor();
    if (!ChildActor)
    {
        UE_LOG(LogTemp, Error, TEXT("OPEN_MASS_CITY_SAMPLE_CHILD_SPAWN_FAILED"));
        return false;
    }
    if (!bRandomizedAppearance)
    {
        UE_LOG(
            LogTemp,
            Error,
            TEXT("OPEN_MASS_CITY_SAMPLE_APPEARANCE_REPLAY_FAILED seed=%d"),
            AppearanceSeed);
        CitySampleCharacter->DestroyChildActor();
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
        if (bLowCostRepresentation)
        {
            Component->SetCastShadow(false);
            if (Component->GetClass()->GetName().Contains(TEXT("Groom")))
            {
                Component->SetVisibility(false, true);
                Component->SetComponentTickEnabled(false);
            }
        }
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

    for (USkeletalMeshComponent* Component : SkeletalComponents)
    {
        if (!Component)
        {
            continue;
        }

        Component->SetForcedLOD(ForcedSkeletalLOD);
        Component->VisibilityBasedAnimTickOption =
            EVisibilityBasedAnimTickOption::OnlyTickPoseWhenRendered;
        if (bLowCostRepresentation)
        {
            Component->SetComponentTickInterval(LowCostAnimationTickInterval);
        }
    }

    DrivingMesh->PlayAnimation(LoadedWalkAnimation, true);
    FRandomStream VisualRandom(AppearanceSeed);
    DrivingMesh->SetPosition(
        VisualRandom.FRandRange(0.0f, LoadedWalkAnimation->GetPlayLength()),
        false);
    DrivingMeshComponent = DrivingMesh;
    IndividualPlayRateScale = VisualRandom.FRandRange(
        1.0f - WalkPlayRateVariation,
        1.0f + WalkPlayRateVariation);
    SetMassSpeedCmPerSecond(ReferenceWalkSpeedCmPerSecond);

    static bool bLoggedFirstReadyActor = false;
    if (!bLoggedFirstReadyActor)
    {
        bLoggedFirstReadyActor = true;
        UE_LOG(
            LogTemp,
            Warning,
            TEXT("OPEN_MASS_CITY_SAMPLE_ACTOR_READY child=%s skeletal_components=%d driving_mesh=%s animation=%s randomized=%s tier=%s forced_lod=%d"),
            *GetNameSafe(ChildActor),
            SkeletalComponents.Num(),
            *GetNameSafe(DrivingMesh),
            *GetNameSafe(LoadedWalkAnimation),
            bRandomizedAppearance ? TEXT("true") : TEXT("false"),
            *GetRepresentationTier().ToString(),
            ForcedSkeletalLOD);
    }

    return true;
}

void AOpenMassCrowdCitySampleActor::SetMassAppearanceSeed(const int32 AppearanceSeed)
{
    if (AppearanceSeed == AppliedAppearanceSeed && DrivingMeshComponent)
    {
        return;
    }
    const double CurrentTimeSeconds = GetWorld() ? GetWorld()->GetTimeSeconds() : 0.0;
    if (AppearanceSeed == FailedAppearanceSeed &&
        CurrentTimeSeconds < NextAppearanceRetryTimeSeconds)
    {
        return;
    }

    DrivingMeshComponent = nullptr;
    const bool bReady = InitializeOfficialCharacter(AppearanceSeed);
    AppliedAppearanceSeed = bReady ? AppearanceSeed : INDEX_NONE;
    FailedAppearanceSeed = bReady ? INDEX_NONE : AppearanceSeed;
    NextAppearanceRetryTimeSeconds =
        bReady ? 0.0 : CurrentTimeSeconds + 1.0;
    SetActorHiddenInGame(!bReady);
}

void AOpenMassCrowdCitySampleActor::SetMassSpeedCmPerSecond(const float SpeedCmPerSecond)
{
    if (!DrivingMeshComponent)
    {
        return;
    }

    const float SafeReferenceSpeed = FMath::Max(ReferenceWalkSpeedCmPerSecond, 1.0f);
    const float SpeedRatio = SpeedCmPerSecond / SafeReferenceSpeed;
    const float DesiredPlayRate = WalkPlayRate * IndividualPlayRateScale * SpeedRatio;
    DrivingMeshComponent->SetPlayRate(FMath::Clamp(
        DesiredPlayRate,
        MinimumWalkPlayRate,
        MaximumWalkPlayRate));
}

FName AOpenMassCrowdCitySampleActor::GetRepresentationTier() const
{
    return bLowCostRepresentation ? TEXT("Low") : TEXT("High");
}
