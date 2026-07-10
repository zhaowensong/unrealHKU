#include "SignalRayManager.h"
#include "SignalSimulationTypes.h"

#include "Components/HierarchicalInstancedStaticMeshComponent.h"
#include "Components/SceneComponent.h"
#include "Engine/StaticMesh.h"
#include "Materials/MaterialInterface.h"
#include "UObject/ConstructorHelpers.h"

namespace
{
constexpr float EngineCylinderHeight = 100.0f;
constexpr int32 PerInstanceMetadataFloats = 2;
}

ASignalRayManager::ASignalRayManager()
{
    PrimaryActorTick.bCanEverTick = false;
    SetIsSpatiallyLoaded(false);

    SceneRoot = CreateDefaultSubobject<USceneComponent>(TEXT("SceneRoot"));
    SetRootComponent(SceneRoot);

    HighStrengthRays = CreateDefaultSubobject<UHierarchicalInstancedStaticMeshComponent>(TEXT("HighStrengthRays"));
    MediumStrengthRays = CreateDefaultSubobject<UHierarchicalInstancedStaticMeshComponent>(TEXT("MediumStrengthRays"));
    LowStrengthRays = CreateDefaultSubobject<UHierarchicalInstancedStaticMeshComponent>(TEXT("LowStrengthRays"));
    ReflectionNodes = CreateDefaultSubobject<UHierarchicalInstancedStaticMeshComponent>(TEXT("ReflectionNodes"));
    BuildingCollisionProxies = CreateDefaultSubobject<UHierarchicalInstancedStaticMeshComponent>(TEXT("BuildingCollisionProxies"));

    HighStrengthRays->SetupAttachment(SceneRoot);
    MediumStrengthRays->SetupAttachment(SceneRoot);
    LowStrengthRays->SetupAttachment(SceneRoot);
    ReflectionNodes->SetupAttachment(SceneRoot);
    BuildingCollisionProxies->SetupAttachment(SceneRoot);

    ConfigureHISM(HighStrengthRays);
    ConfigureHISM(MediumStrengthRays);
    ConfigureHISM(LowStrengthRays);
    ConfigureHISM(ReflectionNodes);
    ConfigureHISM(BuildingCollisionProxies);

    static ConstructorHelpers::FObjectFinder<UStaticMesh> CylinderMesh(
        TEXT("/Engine/BasicShapes/Cylinder.Cylinder"));
    static ConstructorHelpers::FObjectFinder<UStaticMesh> SphereMesh(
        TEXT("/Engine/BasicShapes/Sphere.Sphere"));

    if (CylinderMesh.Succeeded())
    {
        HighStrengthRays->SetStaticMesh(CylinderMesh.Object);
        MediumStrengthRays->SetStaticMesh(CylinderMesh.Object);
        LowStrengthRays->SetStaticMesh(CylinderMesh.Object);
    }
    if (SphereMesh.Succeeded())
    {
        ReflectionNodes->SetStaticMesh(SphereMesh.Object);
    }

    static ConstructorHelpers::FObjectFinder<UStaticMesh> CubeMesh(
        TEXT("/Engine/BasicShapes/Cube.Cube"));
    if (CubeMesh.Succeeded())
    {
        BuildingCollisionProxies->SetStaticMesh(CubeMesh.Object);
    }
    BuildingCollisionProxies->SetCollisionEnabled(ECollisionEnabled::QueryOnly);
    BuildingCollisionProxies->SetCollisionResponseToAllChannels(ECR_Ignore);
    BuildingCollisionProxies->SetCollisionResponseToChannel(ECC_Visibility, ECR_Block);
    BuildingCollisionProxies->SetCollisionResponseToChannel(ECC_Camera, ECR_Block);
    BuildingCollisionProxies->SetVisibility(false, true);
    BuildingCollisionProxies->SetHiddenInGame(true);

    static ConstructorHelpers::FObjectFinder<UMaterialInterface> HighMaterial(
        TEXT("/Game/SignalRayDemo/Materials/MI_SignalRay_Green.MI_SignalRay_Green"));
    static ConstructorHelpers::FObjectFinder<UMaterialInterface> MediumMaterial(
        TEXT("/Game/SignalRayDemo/Materials/MI_SignalRay_Yellow.MI_SignalRay_Yellow"));
    static ConstructorHelpers::FObjectFinder<UMaterialInterface> LowMaterial(
        TEXT("/Game/SignalRayDemo/Materials/MI_SignalRay_Red.MI_SignalRay_Red"));

    if (HighMaterial.Succeeded())
    {
        HighStrengthRays->SetMaterial(0, HighMaterial.Object);
    }
    if (MediumMaterial.Succeeded())
    {
        MediumStrengthRays->SetMaterial(0, MediumMaterial.Object);
        ReflectionNodes->SetMaterial(0, MediumMaterial.Object);
    }
    if (LowMaterial.Succeeded())
    {
        LowStrengthRays->SetMaterial(0, LowMaterial.Object);
    }
}

void ASignalRayManager::ConfigureHISM(UHierarchicalInstancedStaticMeshComponent* Component)
{
    check(Component);
    Component->SetCollisionEnabled(ECollisionEnabled::NoCollision);
    Component->SetGenerateOverlapEvents(false);
    Component->SetCastShadow(false);
    Component->SetMobility(EComponentMobility::Movable);
    Component->SetNumCustomDataFloats(PerInstanceMetadataFloats);
}

void ASignalRayManager::ClearVisualization()
{
    HighStrengthRays->ClearInstances();
    MediumStrengthRays->ClearInstances();
    LowStrengthRays->ClearInstances();
    ReflectionNodes->ClearInstances();
}

void ASignalRayManager::ClearCollisionProxies()
{
    BuildingCollisionProxies->ClearInstances();
}

int32 ASignalRayManager::AddCollisionProxy(
    const FVector Center,
    const FVector Extent,
    const FRotator Rotation,
    const FName ProxyId)
{
    (void)ProxyId;
    const FVector SafeExtent(
        FMath::Max(FMath::Abs(Extent.X), 1.0),
        FMath::Max(FMath::Abs(Extent.Y), 1.0),
        FMath::Max(FMath::Abs(Extent.Z), 1.0));
    const FVector Scale = SafeExtent * (2.0 / 100.0);
    const int32 InstanceIndex = BuildingCollisionProxies->AddInstance(
        FTransform(Rotation, Center, Scale),
        true);
    SetInstanceMetadata(BuildingCollisionProxies, InstanceIndex, 1.0f, InstanceIndex);
    return InstanceIndex;
}

ESignalStrengthBand ASignalRayManager::GetStrengthBand(const float NormalizedStrength) const
{
    if (NormalizedStrength >= 0.67f)
    {
        return ESignalStrengthBand::High;
    }
    if (NormalizedStrength >= 0.34f)
    {
        return ESignalStrengthBand::Medium;
    }
    return ESignalStrengthBand::Low;
}

UHierarchicalInstancedStaticMeshComponent* ASignalRayManager::GetRayComponent(
    const ESignalStrengthBand Band) const
{
    switch (Band)
    {
    case ESignalStrengthBand::High:
        return HighStrengthRays;
    case ESignalStrengthBand::Medium:
        return MediumStrengthRays;
    default:
        return LowStrengthRays;
    }
}

FTransform ASignalRayManager::MakeSegmentTransform(
    const FVector Start,
    const FVector End,
    const float Radius)
{
    const FVector Delta = End - Start;
    const float Length = FMath::Max(Delta.Size(), 1.0f);
    const FVector Direction = Delta.GetSafeNormal(UE_SMALL_NUMBER, FVector::UpVector);
    const FQuat Rotation = FRotationMatrix::MakeFromZ(Direction).ToQuat();
    const FVector Scale(Radius, Radius, Length / EngineCylinderHeight);
    return FTransform(Rotation, (Start + End) * 0.5f, Scale);
}

void ASignalRayManager::SetInstanceMetadata(
    UHierarchicalInstancedStaticMeshComponent* Component,
    const int32 InstanceIndex,
    const float NormalizedStrength,
    const int32 BounceIndex)
{
    Component->SetCustomDataValue(InstanceIndex, 0, FMath::Clamp(NormalizedStrength, 0.0f, 1.0f));
    Component->SetCustomDataValue(InstanceIndex, 1, static_cast<float>(FMath::Max(BounceIndex, 0)), true);
}

int32 ASignalRayManager::AddRaySegment(
    const FVector Start,
    const FVector End,
    const float NormalizedStrength,
    const int32 BounceIndex,
    const FName SourceId)
{
    if (Start.Equals(End, 0.1f))
    {
        return INDEX_NONE;
    }

    const float Strength = FMath::Clamp(NormalizedStrength, 0.0f, 1.0f);
    const float Radius = FMath::Lerp(MinimumRayRadius, MaximumRayRadius, Strength);
    UHierarchicalInstancedStaticMeshComponent* Component = GetRayComponent(GetStrengthBand(Strength));
    const int32 InstanceIndex = Component->AddInstance(MakeSegmentTransform(Start, End, Radius), true);
    SetInstanceMetadata(Component, InstanceIndex, Strength, BounceIndex);
    return InstanceIndex;
}

int32 ASignalRayManager::AddReflectionNode(
    const FVector Position,
    const float NormalizedStrength,
    const int32 BounceIndex)
{
    const float Strength = FMath::Clamp(NormalizedStrength, 0.0f, 1.0f);
    const float Scale = FMath::Lerp(MinimumNodeScale, MaximumNodeScale, Strength);
    const int32 InstanceIndex = ReflectionNodes->AddInstance(
        FTransform(FQuat::Identity, Position, FVector(Scale)),
        true);
    SetInstanceMetadata(ReflectionNodes, InstanceIndex, Strength, BounceIndex);
    return InstanceIndex;
}

void ASignalRayManager::RebuildVisualization(const TArray<FSignalRaySegmentRecord>& Segments)
{
    ClearVisualization();
    for (const FSignalRaySegmentRecord& Segment : Segments)
    {
        if (AddRaySegment(
                Segment.Start,
                Segment.End,
                Segment.NormalizedStrength,
                Segment.BounceIndex,
                Segment.SourceId) != INDEX_NONE && Segment.BounceIndex > 0)
        {
            AddReflectionNode(Segment.End, Segment.NormalizedStrength, Segment.BounceIndex);
        }
    }
}

int32 ASignalRayManager::GetRaySegmentCount() const
{
    return HighStrengthRays->GetInstanceCount()
        + MediumStrengthRays->GetInstanceCount()
        + LowStrengthRays->GetInstanceCount();
}

int32 ASignalRayManager::GetReflectionNodeCount() const
{
    return ReflectionNodes->GetInstanceCount();
}

int32 ASignalRayManager::GetCollisionProxyCount() const
{
    return BuildingCollisionProxies->GetInstanceCount();
}

void ASignalRayManager::ApplySimulationFrame(const FSignalSimulationFrame& Frame)
{
    ClearVisualization();
    for (const FSignalSimulationSegment& Segment : Frame.Segments)
    {
        if (AddRaySegment(
                Segment.Start,
                Segment.End,
                Segment.NormalizedStrength,
                Segment.BounceIndex,
                Segment.SourceId) != INDEX_NONE && Segment.bReflectionHit)
        {
            AddReflectionNode(Segment.End, Segment.NormalizedStrength, Segment.BounceIndex);
        }
    }
}
