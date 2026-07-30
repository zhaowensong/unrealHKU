#pragma once

#include "CoreMinimal.h"
#include "GameFramework/Actor.h"
#include "MassEntityHandle.h"
#include "MassEntityTemplate.h"
#include "MassNavigationFragments.h"
#include "MassZoneGraphNavigationFragments.h"
#include "OpenMassCrowdCentralNetwork.h"
#include "OpenMassCrowdVisualization.h"
#include "ZoneGraphTypes.h"

#include "OpenMassCrowdSpawner.generated.h"

class AZoneGraphData;
class UMassEntityTraitBase;
class UPrimitiveComponent;
class USceneComponent;
class SWidget;
struct FMassVelocityFragment;
struct FMassZoneGraphLaneLocationFragment;
struct FTransformFragment;

/** Selects the proven local patch or a pre-certified Central network asset. */
UENUM(BlueprintType)
enum class EOpenMassCrowdNetworkMode : uint8
{
    LocalCertifiedPatch UMETA(DisplayName = "Local Certified Patch"),
    CentralCertifiedCache UMETA(DisplayName = "Central Certified Cache")
};

/** Evidence gates used to scale Central admission without accepting arbitrary counts. */
UENUM(BlueprintType)
enum class EOpenMassCrowdCentralPopulationGate : uint8
{
    Gate30 UMETA(DisplayName = "30 People"),
    Gate50 UMETA(DisplayName = "50 People"),
    Gate100 UMETA(DisplayName = "100 People"),
    Gate200 UMETA(DisplayName = "200 People"),
    Gate300 UMETA(DisplayName = "300 People")
};

/**
 * Builds a branching runtime pedestrian ZoneGraph from Cesium-grounded points,
 * assigns multi-lane A* trips, and renders the moving MassCrowd population.
 */
UCLASS(BlueprintType)
class OPENMASSCROWD_API AOpenMassCrowdSpawner final : public AActor
{
    GENERATED_BODY()

public:
    AOpenMassCrowdSpawner();

    virtual void Tick(float DeltaSeconds) override;

    UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "Open Mass Crowd")
    TObjectPtr<USceneComponent> SceneRoot;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Open Mass Crowd", meta = (ClampMin = "1", ClampMax = "500"))
    int32 PopulationCount = 30;

    /** Local remains the default so the completed 30-person demo is unchanged. */
    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Open Mass Crowd|Network")
    EOpenMassCrowdNetworkMode NetworkMode = EOpenMassCrowdNetworkMode::LocalCertifiedPatch;

    /** Required only when NetworkMode is CentralCertifiedCache. */
    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Open Mass Crowd|Network")
    TObjectPtr<UOpenMassCrowdCentralNetworkDataAsset> CentralNetworkAsset;

    /** Central admits only one of the staged evidence-gate populations. */
    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Open Mass Crowd|Network|Admission")
    EOpenMassCrowdCentralPopulationGate CentralPopulationGate =
        EOpenMassCrowdCentralPopulationGate::Gate100;

    /** Investor-facing delivery mode; keeps the certified 100-person dataset intact. */
    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Open Mass Crowd|Investor Demo")
    bool bInvestorDeliveryDemoEnabled = true;

    /** Exact active population while investor delivery mode is enabled. */
    UPROPERTY(
        EditAnywhere,
        BlueprintReadWrite,
        Category = "Open Mass Crowd|Investor Demo",
        meta = (ClampMin = "1", ClampMax = "100"))
    int32 InvestorDeliveryPopulation = 50;

    /** Maximum number of faint aggregate links; selected-person link is additional. */
    UPROPERTY(
        EditAnywhere,
        BlueprintReadWrite,
        Category = "Open Mass Crowd|Investor Demo|Visual",
        meta = (ClampMin = "0", ClampMax = "30"))
    int32 InvestorAssociationVisualBudget = 12;

    /** Maximum number of Central Mass entities created in one admission batch. */
    UPROPERTY(
        EditAnywhere,
        BlueprintReadWrite,
        Category = "Open Mass Crowd|Network|Admission",
        meta = (ClampMin = "1", ClampMax = "50"))
    int32 CentralAdmissionBatchSize = 25;

    /** Time between bounded Central admission batches. */
    UPROPERTY(
        EditAnywhere,
        BlueprintReadWrite,
        Category = "Open Mass Crowd|Network|Admission",
        meta = (ClampMin = "0.01", ClampMax = "2.0"))
    float CentralAdmissionBatchInterval = 0.1f;

    /** Draws reviewable nodes, lanes, portals, districts, and rejection markers. */
    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Open Mass Crowd|Network|Debug")
    bool bDrawCentralNetworkOverlay = false;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Open Mass Crowd")
    FVector2D RouteHalfExtent = FVector2D(720.0, 250.0);

    /** Maximum XY distance between collision-grounded samples on each lane. */
    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Open Mass Crowd", meta = (ClampMin = "50.0", ClampMax = "500.0"))
    float NetworkGroundSampleSpacing = 150.0f;

    /** Pedestrian corridor width; runtime generation safely clamps legacy values to 120..160 cm. */
    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Open Mass Crowd", meta = (ClampMin = "120.0", ClampMax = "160.0"))
    float LaneWidth = 120.0f;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Open Mass Crowd", meta = (ClampMin = "5.0", ClampMax = "300.0"))
    float GroundTolerance = 90.0f;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Open Mass Crowd", meta = (ClampMin = "100.0"))
    float TraceHeight = 1600.0f;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Open Mass Crowd", meta = (ClampMin = "100.0"))
    float TraceDepth = 2600.0f;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Open Mass Crowd", meta = (ClampMin = "0.1", ClampMax = "2.0"))
    float GroundCorrectionInterval = 0.2f;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Open Mass Crowd")
    bool bSpawnOnBeginPlay = true;

    /**
     * Temporary engine mannequin by default. Replace these soft references with
     * migrated City Sample VAT variants without touching movement code.
     */
    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Open Mass Crowd|Visual")
    TArray<FOpenMassCrowdVisualConfig> VisualVariants;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Open Mass Crowd|Visual", meta = (ClampMin = "0.0"))
    float VATTimeOffsetSpread = 3.0f;

    UFUNCTION(BlueprintCallable, Category = "Open Mass Crowd")
    void SpawnMassPopulation();

    /** Replace temporary mannequin VAT visuals with Epic City Sample actors. */
    UFUNCTION(BlueprintCallable, Category = "Open Mass Crowd|Visual")
    void ConfigureOfficialCitySampleVisual();

    UFUNCTION(BlueprintPure, Category = "Open Mass Crowd")
    int32 GetSpawnedEntityCount() const { return SpawnedEntities.Num(); }

    UFUNCTION(BlueprintPure, Category = "Open Mass Crowd|Navigation")
    EOpenMassCrowdNetworkMode GetNetworkMode() const { return NetworkMode; }

    UFUNCTION(BlueprintPure, Category = "Open Mass Crowd|Navigation")
    int32 GetRuntimeLaneCount() const { return RuntimeLaneHandles.Num(); }

    UFUNCTION(BlueprintPure, Category = "Open Mass Crowd|Navigation")
    int32 GetRuntimeNetworkNodeCount() const { return RuntimeNetworkNodeCount; }

    UFUNCTION(BlueprintPure, Category = "Open Mass Crowd|Navigation")
    int32 GetRouteAssignmentCount() const { return RouteAssignmentCount; }

    UFUNCTION(BlueprintPure, Category = "Open Mass Crowd|Navigation")
    int32 GetCompletedTripCount() const { return CompletedTripCount; }

    UFUNCTION(BlueprintPure, Category = "Open Mass Crowd|Central")
    int32 GetCentralShortPathChunkCount() const
    {
        return CentralShortPathChunkCount;
    }

    UFUNCTION(BlueprintPure, Category = "Open Mass Crowd|Navigation")
    int32 GetRouteReplanCount() const { return RouteReplanCount; }

    UFUNCTION(BlueprintPure, Category = "Open Mass Crowd|Navigation")
    int32 GetGroundProjectionFailureCount() const { return GroundProjectionFailureCount; }

    UFUNCTION(BlueprintPure, Category = "Open Mass Crowd|Navigation")
    int32 GetGroundRollbackCount() const { return GroundRollbackCount; }

    UFUNCTION(BlueprintPure, Category = "Open Mass Crowd|Navigation")
    int32 GetGroundCenterRecoveryCount() const { return GroundCenterRecoveryCount; }

    UFUNCTION(BlueprintPure, Category = "Open Mass Crowd|Navigation")
    int32 GetGroundUnrecoverableCount() const { return GroundUnrecoverableCount; }

    UFUNCTION(BlueprintPure, Category = "Open Mass Crowd|Navigation")
    int32 GetCurrentUnsupportedVisualCount() const { return CurrentUnsupportedVisualCount; }

    UFUNCTION(BlueprintPure, Category = "Open Mass Crowd|Central|Telemetry")
    int32 GetCentralGroundGuardQueryCount() const { return CentralGroundGuardQueryCount; }

    UFUNCTION(BlueprintPure, Category = "Open Mass Crowd|Central|Telemetry")
    int32 GetCentralGroundCandidateComponentTestCount() const
    {
        return CentralGroundCandidateComponentTestCount;
    }

    UFUNCTION(BlueprintPure, Category = "Open Mass Crowd|Central|Telemetry")
    int32 GetCentralGroundComponentCacheRefreshCount() const
    {
        return CentralGroundComponentCacheRefreshCount;
    }

    UFUNCTION(BlueprintPure, Category = "Open Mass Crowd|Visual")
    int32 GetCurrentHighResRepresentationCount() const { return CurrentHighResRepresentationCount; }

    UFUNCTION(BlueprintPure, Category = "Open Mass Crowd|Visual")
    int32 GetCurrentLowResRepresentationCount() const { return CurrentLowResRepresentationCount; }

    UFUNCTION(BlueprintPure, Category = "Open Mass Crowd|Central|Telemetry")
    int32 GetCentralAdmissionTargetCount() const { return CentralAdmissionTargetCount; }

    UFUNCTION(BlueprintPure, Category = "Open Mass Crowd|Central|Telemetry")
    int32 GetCentralAdmittedEntityCount() const { return CentralAdmittedEntityCount; }

    UFUNCTION(BlueprintPure, Category = "Open Mass Crowd|Central|Telemetry")
    int32 GetCentralSimulatedEntityCount() const { return CentralSimulatedEntityCount; }

    UFUNCTION(BlueprintPure, Category = "Open Mass Crowd|Central|Telemetry")
    int32 GetCentralRepresentedEntityCount() const { return CentralRepresentedEntityCount; }

    UFUNCTION(BlueprintPure, Category = "Open Mass Crowd|Central|Telemetry")
    int32 GetCentralAdmissionBatchCount() const { return CentralAdmissionBatchCount; }

    UFUNCTION(BlueprintPure, Category = "Open Mass Crowd|Central|Telemetry")
    int32 GetCentralMaximumCommittedAdmissionBatchSize() const
    {
        return CentralMaximumCommittedAdmissionBatchSize;
    }

    /** Plan-scoped certified alternatives retained for live Cesium admission. */
    UFUNCTION(BlueprintPure, Category = "Open Mass Crowd|Central|Telemetry")
    int32 GetCentralReserveSpawnSlotCount() const
    {
        return CentralReserveSpawnSlotCount;
    }

    UFUNCTION(BlueprintPure, Category = "Open Mass Crowd|Central|Telemetry")
    int32 GetCentralAdmissionLiveGroundProbeCount() const
    {
        return CentralAdmissionLiveGroundProbeCount;
    }

    UFUNCTION(BlueprintPure, Category = "Open Mass Crowd|Central|Telemetry")
    int32 GetCentralAdmissionLiveGroundRejectCount() const
    {
        return CentralAdmissionLiveGroundRejectCount;
    }

    UFUNCTION(BlueprintPure, Category = "Open Mass Crowd|Central|Telemetry")
    int32 GetCentralAdmissionNoRawSupportCount() const
    {
        return CentralAdmissionNoRawSupportCount;
    }

    UFUNCTION(BlueprintPure, Category = "Open Mass Crowd|Central|Telemetry")
    int32 GetCentralReserveReplacementCount() const
    {
        return CentralReserveReplacementCount;
    }

    UFUNCTION(BlueprintPure, Category = "Open Mass Crowd|Central|Telemetry")
    int32 GetCentralReserveCycleDeferCount() const
    {
        return CentralReserveCycleDeferCount;
    }

    /** Complete deterministic admission plan; all gates use stable prefixes. */
    UFUNCTION(BlueprintPure, Category = "Open Mass Crowd|Central|Telemetry")
    int32 GetCentralPlannedSpawnSlotCount() const
    {
        return CentralPlannedSpawnSlotCount;
    }

    /** Conservative minimum XY body-center clearance across the complete plan. */
    UFUNCTION(BlueprintPure, Category = "Open Mass Crowd|Central|Telemetry")
    float GetCentralMinimumPlannedSpawnClearanceCm() const
    {
        return CentralMinimumPlannedSpawnClearanceCm;
    }

    /** Entities with a valid route that are not intentionally held fail-closed. */
    UFUNCTION(BlueprintPure, Category = "Open Mass Crowd|Central|Telemetry")
    int32 GetCentralExpectedMovingEntityCount() const
    {
        return CentralExpectedMovingEntityCount;
    }

    /** Expected-moving entities whose certified transform advanced at least 10 cm/s. */
    UFUNCTION(BlueprintPure, Category = "Open Mass Crowd|Central|Telemetry")
    int32 GetCentralMovingEntityCount() const { return CentralMovingEntityCount; }

    /** Expected-moving entities that have not advanced for more than five seconds. */
    UFUNCTION(BlueprintPure, Category = "Open Mass Crowd|Central|Telemetry")
    int32 GetCentralStuckEntityCount() const { return CentralStuckEntityCount; }

    UFUNCTION(BlueprintPure, Category = "Open Mass Crowd|Central|Telemetry")
    int32 GetCentralSevereOverlapPairCount() const
    {
        return CentralSevereOverlapPairCount;
    }

    UFUNCTION(BlueprintPure, Category = "Open Mass Crowd|Central|Telemetry")
    int32 GetCentralSevereOverlapAgentCount() const
    {
        return CentralSevereOverlapAgentCount;
    }

    UFUNCTION(BlueprintPure, Category = "Open Mass Crowd|Central|Telemetry")
    int32 GetCentralPeakSevereOverlapPairCount() const
    {
        return CentralPeakSevereOverlapPairCount;
    }

    UFUNCTION(BlueprintPure, Category = "Open Mass Crowd|Central|Telemetry")
    int32 GetCentralPeakSevereOverlapAgentCount() const
    {
        return CentralPeakSevereOverlapAgentCount;
    }

    UFUNCTION(BlueprintPure, Category = "Open Mass Crowd|Central|Telemetry")
    int32 GetCentralSevereOverlapPairObservationCount() const
    {
        return CentralSevereOverlapPairObservationCount;
    }

    UFUNCTION(BlueprintPure, Category = "Open Mass Crowd|Central|Telemetry")
    int32 GetCentralInvalidPositionObservationCount() const
    {
        return CentralInvalidPositionObservationCount;
    }

    /** Minimum center distance in the latest one-second health sample. */
    UFUNCTION(BlueprintPure, Category = "Open Mass Crowd|Central|Telemetry")
    float GetCentralMinimumEntityCenterDistanceCm() const
    {
        return CentralMinimumEntityCenterDistanceCm;
    }

    /** Minimum center distance observed since this Central population started. */
    UFUNCTION(BlueprintPure, Category = "Open Mass Crowd|Central|Telemetry")
    float GetCentralMinimumObservedEntityCenterDistanceCm() const
    {
        return CentralMinimumObservedEntityCenterDistanceCm;
    }

    UFUNCTION(BlueprintPure, Category = "Open Mass Crowd|Central|Telemetry")
    int32 GetCentralHighActorRepresentationCount() const
    {
        return CentralHighActorRepresentationCount;
    }

    UFUNCTION(BlueprintPure, Category = "Open Mass Crowd|Central|Telemetry")
    int32 GetCentralLowActorRepresentationCount() const
    {
        return CentralLowActorRepresentationCount;
    }

    UFUNCTION(BlueprintPure, Category = "Open Mass Crowd|Central|Telemetry")
    int32 GetCentralActorRepresentationCount() const
    {
        return CentralHighActorRepresentationCount +
            CentralLowActorRepresentationCount;
    }

    /** StaticMeshInstance is VAT/ISM in the OpenMassCrowd visual pipeline. */
    UFUNCTION(BlueprintPure, Category = "Open Mass Crowd|Central|Telemetry")
    int32 GetCentralVATRepresentationCount() const
    {
        return CentralVATRepresentationCount;
    }

    /**
     * Runtime-only identity/representation snapshot for one stable Central
     * Mass entity. Evidence scripts use this instead of guessing identity from
     * whichever actor or modular ISM part happens to be visible.
     */
    UFUNCTION(BlueprintPure, Category = "Open Mass Crowd|Central|Evidence")
    FString GetCentralLODEvidenceSnapshot() const;

    /** Selects one of the active stable HK-C-001..100 people and opens its glass card. */
    UFUNCTION(BlueprintCallable, Category = "Open Mass Crowd|Central|Profile")
    bool ShowCentralProfileByStableIndex(int32 StableEntityIndex);

    UFUNCTION(BlueprintCallable, Category = "Open Mass Crowd|Central|Profile")
    void HideCentralProfile();

    /** JSON proof for a currently moving, far-rendered VAT pedestrian. */
    UFUNCTION(BlueprintPure, Category = "Open Mass Crowd|Central|Evidence")
    FString GetCentralVATAnimationEvidenceSnapshot() const;

    /** JSON proof for one explicit stable person, used for paired frame checks. */
    UFUNCTION(BlueprintPure, Category = "Open Mass Crowd|Central|Evidence")
    FString GetCentralVATAnimationEvidenceSnapshotForStableIndex(
        int32 StableEntityIndex) const;

    /** JSON proof for the currently selected stable person/profile. */
    UFUNCTION(BlueprintPure, Category = "Open Mass Crowd|Central|Evidence")
    FString GetCentralProfileEvidenceSnapshot() const;

    /** Complete delivery-mode truth for runtime acceptance and investor demo diagnostics. */
    UFUNCTION(BlueprintPure, Category = "Open Mass Crowd|Investor Demo|Evidence")
    FString GetInvestorDemoEvidenceSnapshot() const;

    UFUNCTION(BlueprintPure, Category = "Open Mass Crowd|Central|Telemetry")
    float GetCentralFrameTimeP50Ms() const { return CentralFrameTimeP50Ms; }

    UFUNCTION(BlueprintPure, Category = "Open Mass Crowd|Central|Telemetry")
    float GetCentralFrameTimeP95Ms() const { return CentralFrameTimeP95Ms; }

    UFUNCTION(BlueprintPure, Category = "Open Mass Crowd|Central|Telemetry")
    float GetCentralFrameTimeMaximumMs() const { return CentralFrameTimeMaximumMs; }

    UFUNCTION(BlueprintPure, Category = "Open Mass Crowd|Central|Telemetry")
    int32 GetCentralFrameTimeSampleCount() const { return CentralFrameTimeSampleCount; }

    UFUNCTION(BlueprintPure, Category = "Open Mass Crowd|Central|Telemetry")
    float GetCentralFrameTimeWindowSeconds() const
    {
        return CentralFrameTimeWindowSeconds;
    }

    UFUNCTION(BlueprintPure, Category = "Open Mass Crowd|Central|Telemetry")
    int32 GetCentralTelemetryObservationCount() const
    {
        return CentralTelemetryObservationCount;
    }

    UFUNCTION(BlueprintPure, Category = "Open Mass Crowd|Central|Telemetry")
    int32 GetCentralLocalConflictResourceCount() const
    {
        return RuntimeCentralLocalConflicts.Num();
    }

    UFUNCTION(BlueprintPure, Category = "Open Mass Crowd|Central|Telemetry")
    int32 GetCentralLocalConflictClusterCount() const
    {
        return RuntimeCentralLocalConflictClusterCount;
    }

    UFUNCTION(BlueprintPure, Category = "Open Mass Crowd|Central|Telemetry")
    int32 GetCentralStrictLocalConflictPairCount() const
    {
        return CentralStrictLocalConflictPairCount;
    }

    UFUNCTION(BlueprintPure, Category = "Open Mass Crowd|Central|Telemetry")
    int32 GetCentralUncoveredLocalConflictPairCount() const
    {
        return CentralUncoveredLocalConflictPairCount;
    }

    UFUNCTION(BlueprintPure, Category = "Open Mass Crowd|Central|Telemetry")
    int32 GetCentralAdmissionClearanceScanCount() const
    {
        return CentralAdmissionClearanceScanCount;
    }

    UFUNCTION(BlueprintPure, Category = "Open Mass Crowd|Central|Telemetry")
    int32 GetCentralAdmissionClearanceViolationCount() const
    {
        return CentralAdmissionClearanceViolationCount;
    }

    UFUNCTION(BlueprintPure, Category = "Open Mass Crowd|Central|Telemetry")
    float GetCentralMaximumConflictWaitSeconds() const
    {
        return CentralMaximumConflictWaitSeconds;
    }

    UFUNCTION(BlueprintPure, Category = "Open Mass Crowd|Central|Telemetry")
    int32 GetCentralConflictWaitReplanCount() const
    {
        return CentralConflictWaitReplanCount;
    }

    /** Runtime streaming/certification can fail a cell closed or restore it explicitly. */
    UFUNCTION(BlueprintCallable, Category = "Open Mass Crowd|Central|Navigation")
    bool SetCentralCellRuntimeAvailable(FName CellId, bool bAvailable);

protected:
    virtual void BeginPlay() override;
    virtual void EndPlay(const EEndPlayReason::Type EndPlayReason) override;

private:
    friend class UOpenMassCrowdPrePathFollowSpacingProcessor;
    friend class UOpenMassCrowdPostAvoidanceReservationProcessor;
    friend class UOpenMassCrowdCertifiedTransformProcessor;

    struct FCentralSpawnSlot
    {
        int32 LaneIndex = INDEX_NONE;
        float DistanceAlongLane = 0.0f;
        FVector Position = FVector::ZeroVector;
        /** Complete-plan XY clearance after removing the slot this may replace. */
        float MinimumOtherPlanClearanceSquared =
            TNumericLimits<float>::Max();
    };

    struct FEntityRouteState
    {
        TArray<FZoneGraphLaneHandle> LanePath;
        /** Exact reverse-lane return core, preceded by the current end-lane anchor. */
        TArray<FZoneGraphLaneHandle> ReturnLanePath;
        TArray<int32> RecentLaneIndices;
        TArray<int32> RecentDestinationLaneIndices;
        int32 CurrentPathIndex = INDEX_NONE;
        float DestinationDistance = 0.0f;
        float ReturnDestinationDistance = 0.0f;
        float PlannedOutboundDistanceCm = 0.0f;
        float PlannedRoundTripDistanceCm = 0.0f;
        int32 CompletedTrips = 0;
        int32 CompletedRoundTrips = 0;
        int32 LastObservedLaneIndex = INDEX_NONE;
        int32 PlannedAvailabilityRevision = 0;
        bool bOnReturnLeg = false;
        bool bSmallComponentFallback = false;
        bool bWaitingForAvailableCell = false;

        void Reset()
        {
            LanePath.Reset();
            ReturnLanePath.Reset();
            CurrentPathIndex = INDEX_NONE;
            DestinationDistance = 0.0f;
            ReturnDestinationDistance = 0.0f;
            PlannedOutboundDistanceCm = 0.0f;
            PlannedRoundTripDistanceCm = 0.0f;
            bOnReturnLeg = false;
            bSmallComponentFallback = false;
            bWaitingForAvailableCell = false;
        }
    };

    struct FRuntimeCentralDistrict
    {
        FName DistrictId = NAME_None;
        TArray<FName> CellIds;
        /** Asset-declared seed component; spawn pool may use other components in the same cell. */
        FName ComponentId = NAME_None;
        TArray<int32> SpawnLaneIndices;
        int32 TargetPopulation = 0;
        int32 AdmittedPopulation = 0;
    };

    /**
     * Conservative conflict interval built only from the immutable certified
     * right-hand support samples.  The two lane intervals share one exclusive
     * resource; same-lane headway remains a separate, non-exclusive control.
     */
    struct FRuntimeCentralLocalConflict
    {
        int32 FirstLaneIndex = INDEX_NONE;
        int32 SecondLaneIndex = INDEX_NONE;
        float FirstBeginDistanceCm = 0.0f;
        float FirstEndDistanceCm = 0.0f;
        float SecondBeginDistanceCm = 0.0f;
        float SecondEndDistanceCm = 0.0f;
        /**
         * A 20 cm hard-conflict interval cut out of a 55 cm whole-track
         * opposing pair. The whole-track relation already protects spawn
         * admission/capacity; this interval is exclusively a runtime lease and
         * must not remove certified spawn samples a second time.
         */
        bool bRuntimeOnlyReservation = false;
    };

    struct FLastValidGroundState
    {
        FTransform Transform = FTransform::Identity;
        FVector RecoveryProbePoint = FVector::ZeroVector;
        FZoneGraphLaneHandle LaneHandle;
        float DistanceAlongLane = 0.0f;
        float LaneLength = 0.0f;
        int32 ConsecutiveMisses = 0;
        bool bHasRecoveryProbe = false;
        bool bUnsupported = false;
        bool bValid = false;
    };

    struct FCentralFrameTimeSample
    {
        double WorldTimeSeconds = 0.0;
        float FrameTimeMilliseconds = 0.0f;
    };

    enum class EInvestorPersonLocationState : uint8
    {
        Outdoor,
        Entering,
        Indoor,
        Exiting
    };

    struct FInvestorStationRuntime
    {
        FName StationId = NAME_None;
        FVector ConfiguredRoofPoint = FVector::ZeroVector;
        FVector ValidatedRoofPoint = FVector::ZeroVector;
        float CoverageRadiusCm = 0.0f;
        FColor DisplayColor = FColor::White;
        /** Physical presentation base offset above the latest live roof hit. */
        float RoofErrorCm = -1.0f;
        /** Cesium LOD drift from the stored Z hint; diagnostic, not mount error. */
        float ConfiguredAnchorAdjustmentCm = 0.0f;
        int32 ValidationAttempts = 0;
        int32 ConsecutiveValidationMisses = 0;
        bool bRoofValidated = false;
    };

    struct FInvestorPersonRuntime
    {
        EInvestorPersonLocationState LocationState =
            EInvestorPersonLocationState::Outdoor;
        int32 ServingStationIndex = INDEX_NONE;
        float SignalQualityPercent = 0.0f;
        float TransitionRemainingSeconds = 0.0f;
        float EntryCooldownRemainingSeconds = 0.0f;
        float VisibilityAlpha = 1.0f;
        int32 CurrentApplicationIndex = 0;
    };

    enum class ECesiumGroundProjectionResult : uint8
    {
        Accepted,
        NoRawSupport,
        NonCesiumFirstBlocker,
        UnwalkableFirstBlocker,
        ElevationMismatch
    };

    bool ProjectToCesiumGround(const FVector& XYPoint, FVector& OutGroundPoint) const;
    ECesiumGroundProjectionResult ProjectToCesiumGroundClassified(
        const FVector& XYPoint,
        FVector& OutGroundPoint) const;
    void RefreshCesiumGroundComponentCache() const;
    void GatherSpatiallyRelevantCesiumComponents(
        const FVector& XYPoint,
        TArray<UPrimitiveComponent*>& OutComponents) const;
    bool HasPedestrianClearance(const FVector& StartGroundedPoint, const FVector& EndGroundedPoint) const;
    bool HasPedestrianCorridorSupport(const FVector& StartGroundedPoint, const FVector& EndGroundedPoint) const;
    bool BuildGroundedRoute(TArray<FVector>& OutRoutePoints) const;
    bool BuildRuntimeZoneGraph(const TArray<FVector>& GroundedRoute);
    bool BuildRuntimeZoneGraphFromCentralCache();
    bool SpawnEntitiesOnLanes();
    bool BeginCentralBatchedAdmission();
    bool AdmitNextCentralBatch();
    bool InitializeCentralEntity(int32 EntityIndex, int32 RuntimeLaneIndex);
    void ContinueCentralAdmission();
    bool PlanNewDestination(int32 EntityIndex);
    bool PlanNewCentralDestination(
        int32 EntityIndex,
        const TSet<int32>* ForbiddenLaneIndices = nullptr);
    bool ActivateCentralReturnRoute(int32 EntityIndex);
    bool RequestNextPath(int32 EntityIndex);
    bool HoldCentralEntityAtCertifiedPosition(int32 EntityIndex, const TCHAR* Reason);
    void RecordCentralCellGroundGuard(
        FName CellId,
        int32 EntityIndex,
        bool bSupported);
    void RefreshCentralUnavailableRoutes();
    void QueueCentralConflictReplan(
        int32 EntityIndex,
        const TSet<int32>& ForbiddenLaneIndices);
    void ProcessCentralConflictReplans();
    bool IsCentralRuntimeLaneAvailable(int32 RuntimeLaneIndex) const;
    /**
     * Clamp every Central Mass transform to the current position on its
     * offline Cesium-certified 10 cm lane polyline.  Both spawned actors and
     * VAT/ISM therefore consume one smooth, supported transform every frame;
     * the staggered live queries remain a streaming-validity guard instead of
     * the visual animation clock.
     */
    void ConstrainCentralTransformsToCertifiedLanes();
    /** Immutable free-flow speed shared by path requests and headway control. */
    float GetCentralCruiseSpeedCmPerSecond(int32 EntityIndex) const;
    bool ConstrainCentralEntityTransform(
        int32 EntityIndex,
        FTransformFragment& TransformFragment,
        FMassZoneGraphLaneLocationFragment& LaneLocation,
        FMassVelocityFragment* Velocity);
    void RecordCentralFrameTime(float DeltaSeconds);
    void RecordCentralTelemetry(bool bForceLog = false);
    void ResetCentralSessionTelemetry();
    void ResetCentralRuntimeTelemetry();
    void RefreshCompletedPaths();
    void CorrectMassGrounding();
    void SyncVisualActorsToMass();
    void UpdateCentralProfileInteraction();
    int32 GetRequestedCentralPopulation() const;
    void EnsureInvestorDemoInitialized();
    void UpdateInvestorDemo(float DeltaSeconds);
    void UpdateInvestorPersonStates(float DeltaSeconds);
    void DrawInvestorDemoVisuals() const;
    bool ValidateInvestorStationRoof(FInvestorStationRuntime& Station);
    void SuppressLegacySignalActors();
    void RestoreLegacySignalActors();
    void ShowInvestorKPI();
    void HideInvestorKPI();
    FString GetInvestorPersonLocationLabel(int32 StableEntityIndex) const;
    FString GetInvestorPersonStationLabel(int32 StableEntityIndex) const;
    FString GetInvestorPersonSignalLabel(int32 StableEntityIndex) const;
    FString GetInvestorPersonApplication(int32 StableEntityIndex) const;
    int32 GetInvestorConnectedCount() const;
    int32 GetInvestorIndoorCount() const;
    int32 GetInvestorValidatedStationCount() const;
    void ScheduleCentralSpawnRetry(const TCHAR* Reason);
    void RetrySpawn();
    void DestroyRuntimePopulation();

    UPROPERTY(Transient)
    TObjectPtr<AZoneGraphData> RuntimeZoneGraphData;

    UPROPERTY(Transient)
    TArray<TObjectPtr<UMassEntityTraitBase>> RuntimeTraits;

    TArray<FMassEntityHandle> SpawnedEntities;
    TArray<FZoneGraphLaneHandle> RuntimeLaneHandles;
    /** Immutable certified length used by route-contiguous conflict claims. */
    TArray<float> RuntimeCentralLaneLengthsCm;
    TArray<FName> RuntimeCentralLaneIds;
    TArray<FName> RuntimeCentralLaneCellIds;
    TArray<FName> RuntimeCentralLaneComponentIds;
    TArray<FName> RuntimeCentralLaneFromNodeIds;
    TArray<FName> RuntimeCentralLaneToNodeIds;
    /**
     * Direction-specific equivalence class for complete certified support
     * tracks whose synchronized geometry is closer than one Central body
     * clearance. Near-duplicate semantic recovery lanes share one class.
     */
    TArray<int32> RuntimeCentralPhysicalTrackIndices;
    /** Direct (non-transitive) same-direction whole-track conflicts. */
    TArray<TArray<int32>> RuntimeCentralSameDirectionPhysicalLaneIndices;
    /**
     * Direct 55 cm whole-track opposing relation for spawn admission and
     * occupancy capacity. Runtime movement never locks this whole relation;
     * sub-20 cm stations are separate local resources below.
     */
    TArray<TArray<int32>> RuntimeCentralOpposingPhysicalLaneIndices;
    /** Every certified-sample local conflict not covered by same-lane headway. */
    TArray<FRuntimeCentralLocalConflict> RuntimeCentralLocalConflicts;
    /** Cluster id for each interval; overlapping expanded intervals are atomic. */
    TArray<int32> RuntimeCentralLocalConflictClusterIndices;
    TArray<TArray<int32>> RuntimeCentralLocalConflictIndicesByCluster;
    int32 RuntimeCentralLocalConflictClusterCount = 0;
    /** Direct resource indices for each lane; never a transitive union. */
    TArray<TArray<int32>> RuntimeCentralLocalConflictIndicesByLane;
    /** Precomputed body-expanded overlap closure for each lane/resource seed. */
    TArray<TMap<int32, TArray<int32>>>
        RuntimeCentralLocalConflictClosuresByLane;
    /** Coarse direct collision adjacency used only by occupancy-aware A*. */
    TArray<TArray<int32>> RuntimeCentralCollisionConflictLaneIndices;
    TArray<int32> RuntimeCentralReverseLaneIndices;
    TArray<uint8> RuntimeCentralCrossingFlags;
    TMap<FName, int32> RuntimeCentralLaneIndicesById;
    TSet<FName> RuntimeUnavailableCentralCellIds;
    TSet<FName> RuntimeKnownCentralCellIds;
    TMap<FName, int32> RuntimeCentralCellRecoveryGuardCounts;
    TMap<FName, TSet<int32>> RuntimeCentralCellFailedGuardEntities;
    TArray<FRuntimeCentralDistrict> RuntimeCentralDistricts;
    TArray<int32> CentralSpawnLanePlan;
    /** Deterministic collision-cleared distance paired with each spawn lane. */
    TArray<float> CentralSpawnDistancePlan;
    /** Exact collision-certified support-track point paired with each lane/distance. */
    TArray<FVector> CentralSpawnPositionPlan;
    TArray<int32> CentralSpawnDistrictPlan;
    /**
     * Certified same-district alternatives for each stable plan index.  Each
     * reserve is collision-safe against the other 299 slots of the complete
     * plan; live admission still reprojects its exact XY and rechecks every
     * current/planned occupant before replacing the original slot.
     */
    TArray<TArray<FCentralSpawnSlot>> CentralReserveSpawnSlotsByPlanIndex;
    /** Rotating bounded-search start paired with each plan-scoped reserve list. */
    TArray<int32> CentralReserveSpawnCursorsByPlanIndex;
    /**
     * Exact live first-hit positions consumed by InitializeCentralEntity in
     * the same successful admission pass; cached certified points above stay
     * immutable and deferred batches never reuse a prior-frame probe.
     */
    TArray<FVector> CentralSpawnLivePositionPlan;
    TArray<uint8> CentralSpawnLivePositionValid;
    TArray<FMassEntityTemplateID> CentralRuntimeTemplateIds;
    /** Names aligned with CentralRuntimeTemplateIds. */
    TArray<FName> CentralRuntimeVariantNames;
    TArray<int32> CentralRuntimeVariantRemainingCounts;
    /** Runtime-variant index owned by each stable SpawnedEntities slot. */
    TArray<int32> CentralEntityVisualVariantIndices;
    /**
     * Exact-XY live support gate for the deterministic investor sidewalk band
     * owned by each stable entity. A zero value renders the certified track.
     */
    TArray<uint8> CentralPresentationOffsetValid;
    TArray<FEntityRouteState> EntityRouteStates;
    /** Persistent fair-wait age for each stable entity's current corridor request. */
    TArray<float> RuntimeCentralCorridorWaitSeconds;
    /** Destination lane paired with RuntimeCentralCorridorWaitSeconds. */
    TArray<int32> RuntimeCentralCorridorWaitLaneIndices;
    /** Persistent fair-wait age for the current local conflict resource. */
    TArray<float> RuntimeCentralLocalConflictWaitSeconds;
    /** Resource paired with RuntimeCentralLocalConflictWaitSeconds. */
    TArray<int32> RuntimeCentralLocalConflictWaitResourceIndices;
    /** Deferred out of the Mass processor and consumed from the actor Tick. */
    TMap<int32, TSet<int32>> PendingCentralConflictReplans;
    /** Last exact-XY Cesium-supported Mass and lane state for each stable entity index. */
    TArray<FLastValidGroundState> LastValidGroundStates;
    /** Final collision-safe certified state from the immediately previous frame. */
    TArray<FLastValidGroundState> RuntimeCentralPreviousFrameStates;
    /** Navigation state aligned atomically with RuntimeCentralPreviousFrameStates. */
    TArray<FMassZoneGraphShortPathFragment>
        RuntimeCentralPreviousFrameShortPaths;
    TArray<FMassMoveTargetFragment> RuntimeCentralPreviousFrameMoveTargets;
    TArray<uint8> RuntimeCentralPreviousFrameNavigationValid;
    /**
     * A collision-safe certified state retained at least 80 cm behind the
     * current state.  It gives a pedestrian room to yield when two certified
     * tracks geometrically pinch below the 20 cm hard-clearance gate and an
     * immediate one-frame rollback cannot let either participant pass.
     */
    TArray<FLastValidGroundState> RuntimeCentralYieldAnchorStates;
    TArray<FMassZoneGraphShortPathFragment>
        RuntimeCentralYieldAnchorShortPaths;
    TArray<FMassMoveTargetFragment> RuntimeCentralYieldAnchorMoveTargets;
    TArray<uint8> RuntimeCentralYieldAnchorNavigationValid;
    FRandomStream RouteRandomStream;
    FTimerHandle SpawnRetryTimer;
    FTimerHandle CentralAdmissionTimer;
    int32 RuntimeNetworkNodeCount = 0;
    int32 RouteAssignmentCount = 0;
    int32 CompletedTripCount = 0;
    int32 CentralShortPathChunkCount = 0;
    int32 RouteReplanCount = 0;
    int32 GroundProjectionFailureCount = 0;
    int32 GroundRollbackCount = 0;
    int32 GroundCenterRecoveryCount = 0;
    int32 GroundUnrecoverableCount = 0;
    int32 CurrentUnsupportedVisualCount = 0;
    int32 MaxConsecutiveGroundMisses = 0;
    int32 CentralGroundGuardQueryCount = 0;
    mutable int32 CentralGroundCandidateComponentTestCount = 0;
    mutable int32 CentralGroundComponentCacheRefreshCount = 0;
    int32 CurrentHighResRepresentationCount = 0;
    int32 CurrentLowResRepresentationCount = 0;
    int32 CentralAdmissionTargetCount = 0;
    int32 CentralAdmittedEntityCount = 0;
    /**
     * Central admission is transactional: Mass entities remain parked on their
     * certified spawn samples until the complete population has committed.
     * This flag is the single gate for every automatic path/replan entry point.
     */
    bool bCentralAdmissionReleased = false;
    int32 CentralSimulatedEntityCount = 0;
    int32 CentralRepresentedEntityCount = 0;
    int32 CentralAdmissionBatchCount = 0;
    int32 CentralMaximumCommittedAdmissionBatchSize = 0;
    int32 CentralReserveSpawnSlotCount = 0;
    int32 CentralAdmissionLiveGroundProbeCount = 0;
    int32 CentralAdmissionLiveGroundRejectCount = 0;
    int32 CentralAdmissionNoRawSupportCount = 0;
    int32 CentralReserveReplacementCount = 0;
    int32 CentralReserveCycleDeferCount = 0;
    int32 CentralPlannedSpawnSlotCount = 0;
    int32 CentralExpectedMovingEntityCount = 0;
    int32 CentralMovingEntityCount = 0;
    int32 CentralStuckEntityCount = 0;
    int32 CentralSevereOverlapPairCount = 0;
    int32 CentralSevereOverlapAgentCount = 0;
    int32 CentralPeakSevereOverlapPairCount = 0;
    int32 CentralPeakSevereOverlapAgentCount = 0;
    int32 CentralSevereOverlapPairObservationCount = 0;
    /** Session-lifetime valid entities omitted from a per-frame pair scan. */
    int32 CentralInvalidPositionObservationCount = 0;
    int32 CentralStrictLocalConflictPairCount = 0;
    int32 CentralUncoveredLocalConflictPairCount = 0;
    int32 CentralAdmissionClearanceScanCount = 0;
    int32 CentralAdmissionClearanceViolationCount = 0;
    /** Cumulative per-frame entities held outside a certified 20 cm node zone. */
    int32 CentralNodeReservationHoldCount = 0;
    /** Legacy whole-corridor hold counter; runtime whole-track locking is off. */
    int32 CentralCorridorDirectionHoldCount = 0;
    /** Cumulative local certified-geometry resource holds. */
    int32 CentralLocalConflictHoldCount = 0;
    /** Occupancy-aware route changes used to break bounded conflict waits. */
    int32 CentralConflictWaitReplanCount = 0;
    float CentralMaximumConflictWaitSeconds = 0.0f;
    int32 CentralHighActorRepresentationCount = 0;
    int32 CentralLowActorRepresentationCount = 0;
    int32 CentralVATRepresentationCount = 0;
    int32 CentralFrameTimeSampleCount = 0;
    int32 CentralTelemetryObservationCount = 0;
    float CentralMinimumEntityCenterDistanceCm = -1.0f;
    float CentralMinimumObservedEntityCenterDistanceCm = -1.0f;
    float CentralMinimumPlannedSpawnClearanceCm = -1.0f;
    float CentralFrameTimeP50Ms = 0.0f;
    float CentralFrameTimeP95Ms = 0.0f;
    float CentralFrameTimeMaximumMs = 0.0f;
    float CentralFrameTimeWindowSeconds = 0.0f;
    float CentralTelemetrySampleAccumulator = 0.0f;
    TArray<FVector> CentralTelemetryLastCertifiedPositions;
    TArray<float> CentralTelemetryStationarySeconds;
    TArray<uint8> CentralTelemetryPositionValid;
    TArray<FCentralFrameTimeSample> CentralFrameTimeSamples;
    int32 CentralFrameTimeFirstSampleIndex = 0;
    int32 CentralAvailabilityRevision = 0;
    int32 CentralGroundGuardBucketCursor = 0;
    int32 CentralWaitingRouteRetryCursor = 0;
    int32 LastLoggedCentralRepresentedCount = INDEX_NONE;
    int32 SelectedCentralProfileEntityIndex = INDEX_NONE;
    bool bCentralProfileInputConfigured = false;
    bool bCentralProfileHasPreviousControlRotation = false;
    FRotator CentralProfilePreviousControlRotation = FRotator::ZeroRotator;
    TSharedPtr<SWidget> CentralProfileViewportWidget;
    TSharedPtr<SWidget> InvestorKPIViewportWidget;
    TArray<FInvestorStationRuntime> InvestorStations;
    TArray<FInvestorPersonRuntime> InvestorPeople;
    FVector InvestorBuildingPortalLocation = FVector::ZeroVector;
    TArray<TWeakObjectPtr<AActor>> InvestorSuppressedSignalActors;
    TArray<uint8> InvestorSuppressedSignalPreviousHidden;
    float InvestorNetworkUpdateAccumulator = 0.0f;
    float InvestorRoofValidationAccumulator = 0.0f;
    float InvestorProfileRefreshAccumulator = 0.0f;
    float InvestorVisualRefreshAccumulator = 0.0f;
    float InvestorElapsedSeconds = 0.0f;
    int32 InvestorBuildingEntryCount = 0;
    int32 InvestorBuildingExitCount = 0;
    int32 InvestorStationReacquisitionCount = 0;
    bool bInvestorDemoInitialized = false;
    bool bInvestorAutoProfileOpened = false;
    int32 GroundRetryCount = 0;
    float PathRefreshAccumulator = 0.0f;
    float GroundCorrectionAccumulator = 0.0f;
    mutable TMap<
        FIntPoint,
        TArray<TWeakObjectPtr<UPrimitiveComponent>>> CesiumGroundComponentGrid;
    mutable TArray<TWeakObjectPtr<UPrimitiveComponent>> CesiumGroundLargeComponents;
    mutable double CesiumGroundComponentCacheRefreshWorldTime = -1.0;
    mutable bool bCesiumGroundComponentCacheInitialized = false;
};
