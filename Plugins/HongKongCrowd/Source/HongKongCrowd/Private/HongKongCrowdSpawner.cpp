#include "HongKongCrowdSpawner.h"

#include "HongKongCrowdPedestrian.h"

#include "AIController.h"
#include "Components/CapsuleComponent.h"
#include "Components/SceneComponent.h"
#include "Components/StaticMeshComponent.h"
#include "Engine/StaticMesh.h"
#include "Navigation/CrowdFollowingComponent.h"
#include "NavigationSystem.h"
#include "TimerManager.h"
#include "UObject/UObjectGlobals.h"

namespace
{
float Halton(int32 Index, int32 Base)
{
    float Fraction = 1.0f;
    float Result = 0.0f;
    while (Index > 0)
    {
        Fraction /= static_cast<float>(Base);
        Result += Fraction * static_cast<float>(Index % Base);
        Index /= Base;
    }
    return Result;
}
}

AHongKongCrowdSpawner::AHongKongCrowdSpawner()
{
    PrimaryActorTick.bCanEverTick = false;

    USceneComponent* Root = CreateDefaultSubobject<USceneComponent>(TEXT("Root"));
    Root->SetMobility(EComponentMobility::Static);
    SetRootComponent(Root);

    WalkableProxy = CreateDefaultSubobject<UStaticMeshComponent>(TEXT("WalkableProxy"));
    WalkableProxy->SetupAttachment(Root);
    WalkableProxy->SetCollisionEnabled(ECollisionEnabled::QueryAndPhysics);
    WalkableProxy->SetCollisionResponseToAllChannels(ECR_Block);
    WalkableProxy->SetCanEverAffectNavigation(true);
    WalkableProxy->SetHiddenInGame(true);
    WalkableProxy->SetVisibility(false, true);
    WalkableProxy->SetMobility(EComponentMobility::Static);

    if (UStaticMesh* Cube = LoadObject<UStaticMesh>(
        nullptr, TEXT("/Engine/BasicShapes/Cube.Cube")))
    {
        WalkableProxy->SetStaticMesh(Cube);
    }

    Tags.Add(TEXT("HK_Crowd_Demo"));
    UpdateProxySize();
}

void AHongKongCrowdSpawner::OnConstruction(const FTransform& Transform)
{
    Super::OnConstruction(Transform);
    UpdateProxySize();
}

void AHongKongCrowdSpawner::BeginPlay()
{
    Super::BeginPlay();

    if (bSpawnOnBeginPlay)
    {
        GetWorldTimerManager().SetTimer(
            SpawnTimerHandle, this, &AHongKongCrowdSpawner::SpawnPopulation, 1.5f, false);
    }
}

void AHongKongCrowdSpawner::UpdateProxySize()
{
    if (!WalkableProxy)
    {
        return;
    }

    const float Thickness = 12.0f;
    WalkableProxy->SetRelativeLocation(FVector(0.0, 0.0, -Thickness * 0.5f));
    WalkableProxy->SetRelativeScale3D(
        FVector(AreaHalfExtent.X / 50.0f, AreaHalfExtent.Y / 50.0f, Thickness / 100.0f));
}

bool AHongKongCrowdSpawner::ValidateCesiumGround(
    const FVector& XYPoint, FVector& OutGroundPoint) const
{
    FHitResult Hit;
    FCollisionQueryParams Params(SCENE_QUERY_STAT(HongKongCrowdGround), true, this);
    Params.AddIgnoredActor(this);

    const FVector Start(XYPoint.X, XYPoint.Y, GetActorLocation().Z + TraceHeight);
    const FVector End(XYPoint.X, XYPoint.Y, GetActorLocation().Z - TraceDepth);
    if (!GetWorld()->LineTraceSingleByChannel(Hit, Start, End, ECC_Visibility, Params))
    {
        return false;
    }

    const UPrimitiveComponent* HitComponent = Hit.GetComponent();
    const bool bCesiumComponent = HitComponent &&
        HitComponent->GetClass()->GetName().Contains(TEXT("CesiumGltfPrimitiveComponent"));
    const bool bFlatEnough = Hit.ImpactNormal.Z >= 0.72f;
    const bool bNearProxy = FMath::Abs(Hit.ImpactPoint.Z - GetActorLocation().Z) <= GroundTolerance;

    if (!bCesiumComponent || !bFlatEnough || !bNearProxy)
    {
        return false;
    }

    OutGroundPoint = Hit.ImpactPoint;
    return true;
}

bool AHongKongCrowdSpawner::FindValidPoint(int32 Seed, FVector& OutPoint) const
{
    for (int32 Attempt = 0; Attempt < 40; ++Attempt)
    {
        const int32 Index = Seed * 43 + Attempt + 1;
        const float X = (Halton(Index, 2) * 2.0f - 1.0f) * AreaHalfExtent.X * 0.88f;
        const float Y = (Halton(Index, 3) * 2.0f - 1.0f) * AreaHalfExtent.Y * 0.88f;
        const FVector Candidate = GetActorLocation() + FVector(X, Y, 0.0f);

        FVector GroundPoint;
        if (ValidateCesiumGround(Candidate, GroundPoint))
        {
            OutPoint = FVector(Candidate.X, Candidate.Y, GetActorLocation().Z);
            return true;
        }
    }

    return false;
}

void AHongKongCrowdSpawner::SpawnPopulation()
{
    if (!GetWorld() || SpawnedPedestrians.Num() > 0)
    {
        return;
    }

    FVector CenterGround;
    if (!ValidateCesiumGround(GetActorLocation(), CenterGround))
    {
        ++GroundRetryCount;
        if (GroundRetryCount <= 60)
        {
            if (GroundRetryCount == 1 || GroundRetryCount % 5 == 0)
            {
                UE_LOG(
                    LogTemp,
                    Warning,
                    TEXT("HK_CROWD_WAIT_GROUND retry=%d/60 Cesium collision is still streaming"),
                    GroundRetryCount);
            }
            GetWorldTimerManager().SetTimer(
                SpawnTimerHandle, this, &AHongKongCrowdSpawner::SpawnPopulation, 1.0f, false);
            return;
        }

        UE_LOG(LogTemp, Error, TEXT("HK_CROWD_ABORT Cesium ground unavailable after 60 retries"));
        return;
    }

    GroundRetryCount = 0;

    const float CapsuleHalfHeight = 90.0f;
    for (int32 Index = 0; Index < PopulationCount; ++Index)
    {
        FVector GroundedPoint;
        if (!FindValidPoint(Index + 1, GroundedPoint))
        {
            UE_LOG(LogTemp, Warning, TEXT("HK_CROWD_REJECT spawn=%d no grounded candidate"), Index);
            continue;
        }

        const FVector SpawnLocation = GroundedPoint + FVector(0.0, 0.0, CapsuleHalfHeight + 2.0f);
        FActorSpawnParameters Params;
        Params.SpawnCollisionHandlingOverride = ESpawnActorCollisionHandlingMethod::AdjustIfPossibleButAlwaysSpawn;
        AHongKongCrowdPedestrian* Pedestrian = GetWorld()->SpawnActor<AHongKongCrowdPedestrian>(
            AHongKongCrowdPedestrian::StaticClass(), SpawnLocation, FRotator::ZeroRotator, Params);
        if (!Pedestrian)
        {
            continue;
        }

        Pedestrian->Tags.Add(TEXT("HK_Crowd_Pedestrian"));
        Pedestrian->SpawnDefaultController();
        SpawnedPedestrians.Add(Pedestrian);

        if (AAIController* Controller = Cast<AAIController>(Pedestrian->GetController()))
        {
            if (UCrowdFollowingComponent* Crowd = Cast<UCrowdFollowingComponent>(Controller->GetPathFollowingComponent()))
            {
                Crowd->SetCrowdAvoidanceQuality(ECrowdAvoidanceQuality::High);
                Crowd->SetCrowdCollisionQueryRange(500.0f);
                Crowd->SetCrowdSeparation(true);
                Crowd->SetCrowdSeparationWeight(3.0f);
            }
        }

        AssignDestination(Pedestrian, Index + 101);
    }

    UE_LOG(
        LogTemp,
        Warning,
        TEXT("HK_CROWD_READY requested=%d spawned=%d center=(%.2f,%.2f,%.2f)"),
        PopulationCount,
        SpawnedPedestrians.Num(),
        GetActorLocation().X,
        GetActorLocation().Y,
        GetActorLocation().Z);

    GetWorldTimerManager().SetTimer(
        UpdateTimerHandle, this, &AHongKongCrowdSpawner::UpdateCrowd, 1.0f, true);
}

void AHongKongCrowdSpawner::AssignDestination(
    AHongKongCrowdPedestrian* Pedestrian, int32 Seed)
{
    if (!IsValid(Pedestrian))
    {
        return;
    }

    FVector Candidate;
    if (!FindValidPoint(Seed, Candidate))
    {
        return;
    }

    UNavigationSystemV1* NavSystem = FNavigationSystem::GetCurrent<UNavigationSystemV1>(GetWorld());
    FNavLocation Projected;
    if (!NavSystem || !NavSystem->ProjectPointToNavigation(Candidate, Projected, FVector(120.0, 120.0, 250.0)))
    {
        return;
    }

    if (AAIController* Controller = Cast<AAIController>(Pedestrian->GetController()))
    {
        Controller->MoveToLocation(Projected.Location, 55.0f, true, true, true, false, nullptr, true);
        Destinations.Add(Pedestrian, Projected.Location);
    }
}

void AHongKongCrowdSpawner::UpdateCrowd()
{
    ++DestinationSequence;

    for (int32 Index = SpawnedPedestrians.Num() - 1; Index >= 0; --Index)
    {
        AHongKongCrowdPedestrian* Pedestrian = SpawnedPedestrians[Index];
        if (!IsValid(Pedestrian))
        {
            SpawnedPedestrians.RemoveAtSwap(Index);
            continue;
        }

        const FVector* Destination = Destinations.Find(Pedestrian);
        const bool bArrived = Destination &&
            FVector::DistSquared2D(Pedestrian->GetActorLocation(), *Destination) < FMath::Square(100.0f);
        const bool bStalled = Pedestrian->GetVelocity().SizeSquared2D() < FMath::Square(8.0f);
        if (bArrived || bStalled || (DestinationSequence + Index) % 7 == 0)
        {
            AssignDestination(Pedestrian, DestinationSequence * 67 + Index + 301);
        }
    }
}

TArray<AHongKongCrowdPedestrian*> AHongKongCrowdSpawner::GetPedestrians() const
{
    TArray<AHongKongCrowdPedestrian*> Result;
    Result.Reserve(SpawnedPedestrians.Num());
    for (AHongKongCrowdPedestrian* Pedestrian : SpawnedPedestrians)
    {
        Result.Add(Pedestrian);
    }
    return Result;
}
