#include "OpenMassCrowdVisualization.h"

#include "OpenMassCrowdSpawner.h"
#include "OpenMassCrowdTrait.h"

#include "AnimToTextureInstancePlaybackHelpers.h"
#include "MassCommonFragments.h"
#include "MassCrowdVisualizationProcessor.h"
#include "MassCrowdSubsystem.h"
#include "MassCrowdFragments.h"
#include "MassExecutionContext.h"
#include "MassLODFragments.h"
#include "MassMovementFragments.h"
#include "MassNavigationFragments.h"
#include "MassRepresentationFragments.h"
#include "MassRepresentationSubsystem.h"
#include "MassRepresentationTypes.h"
#include "MassSimulationLOD.h"
#include "MassUpdateISMProcessor.h"
#include "MassEntityTemplateRegistry.h"
#include "MassZoneGraphNavigationFragments.h"
#include "MassZoneGraphNavigationProcessors.h"
#include "Movement/MassMovementProcessors.h"
#include "ZoneGraphSubsystem.h"

UOpenMassCrowdPrePathFollowSpacingProcessor::
    UOpenMassCrowdPrePathFollowSpacingProcessor()
    : EntityQuery(*this)
{
    ExecutionFlags = static_cast<int32>(EProcessorExecutionFlags::AllNetModes);
    ProcessingPhase = EMassProcessingPhase::PrePhysics;
    bAutoRegisterWithProcessingPhases = true;
    bRequiresGameThreadExecution = true;

    // DesiredSpeed is consumed by this exact engine processor.  Reserving the
    // following distance here prevents an overlap; it does not move or push an
    // entity after the certified transform has already been produced.
    ExecutionOrder.ExecuteInGroup = UE::Mass::ProcessorGroupNames::Tasks;
    ExecutionOrder.ExecuteBefore.Add(
        UMassZoneGraphPathFollowProcessor::StaticClass()->GetFName());
}

void UOpenMassCrowdPrePathFollowSpacingProcessor::ConfigureQueries(
    const TSharedRef<FMassEntityManager>& EntityManager)
{
    EntityQuery.AddTagRequirement<FOpenMassCrowdTag>(
        EMassFragmentPresence::All);
    EntityQuery.AddRequirement<FOpenMassCrowdCentralVisualOwnerFragment>(
        EMassFragmentAccess::ReadOnly);
    EntityQuery.AddRequirement<FTransformFragment>(
        EMassFragmentAccess::ReadOnly);
    EntityQuery.AddRequirement<FMassZoneGraphLaneLocationFragment>(
        EMassFragmentAccess::ReadOnly);
    EntityQuery.AddRequirement<FMassZoneGraphShortPathFragment>(
        EMassFragmentAccess::ReadOnly);
    EntityQuery.AddRequirement<FAgentRadiusFragment>(
        EMassFragmentAccess::ReadOnly);
    EntityQuery.AddRequirement<FMassMoveTargetFragment>(
        EMassFragmentAccess::ReadWrite);
    EntityQuery.AddRequirement<FMassSimulationLODFragment>(
        EMassFragmentAccess::ReadOnly,
        EMassFragmentPresence::Optional);
    EntityQuery.AddRequirement<FMassSimulationVariableTickFragment>(
        EMassFragmentAccess::ReadOnly,
        EMassFragmentPresence::Optional);
    EntityQuery.AddChunkRequirement<FMassSimulationVariableTickChunkFragment>(
        EMassFragmentAccess::ReadOnly,
        EMassFragmentPresence::Optional);
}

void UOpenMassCrowdPrePathFollowSpacingProcessor::Execute(
    FMassEntityManager& EntityManager,
    FMassExecutionContext& Context)
{
    struct FCentralSpacingRecord
    {
        FMassEntityHandle Entity;
        AOpenMassCrowdSpawner* Spawner = nullptr;
        int32 EntityIndex = INDEX_NONE;
        int32 PresentationBandIndex = 0;
        int32 LaneIndex = INDEX_NONE;
        int32 PhysicalTrackIndex = INDEX_NONE;
        int32 NextLaneIndex = INDEX_NONE;
        int32 NextPhysicalTrackIndex = INDEX_NONE;
        FName FromNodeId = NAME_None;
        FName ToNodeId = NAME_None;
        FName TransitionNodeId = NAME_None;
        FVector Position = FVector::ZeroVector;
        float ProgressCm = 0.0f;
        float LaneLengthCm = 0.0f;
        float ProgressFraction = 0.0f;
        float DistanceToNodeCm = TNumericLimits<float>::Max();
        float RadiusCm = 0.0f;
        float CruiseSpeedCmPerSecond = 0.0f;
        float TickDeltaSeconds = 0.0f;
        float DistanceToTransitionCm = TNumericLimits<float>::Max();
        float TargetSpeedCmPerSecond = 0.0f;
        /** Current lane followed by the remaining authoritative route lanes. */
        TArray<int32> PlannedLaneIndices;
        bool bActiveMove = false;
        bool bWillAdvance = false;
        bool bHasOutgoingTransition = false;
        /** Entry headway has stopped this entity behind the transition. */
        bool bDestinationStartOccupied = false;
    };

    TArray<FCentralSpacingRecord> Records;
    EntityQuery.ForEachEntityChunk(
        Context,
        [&Records](FMassExecutionContext& Context)
        {
            const TConstArrayView<FOpenMassCrowdCentralVisualOwnerFragment>
                OwnerList = Context.GetFragmentView<
                    FOpenMassCrowdCentralVisualOwnerFragment>();
            const TConstArrayView<FTransformFragment> TransformList =
                Context.GetFragmentView<FTransformFragment>();
            const TConstArrayView<FMassZoneGraphLaneLocationFragment> LaneList =
                Context.GetFragmentView<FMassZoneGraphLaneLocationFragment>();
            const TConstArrayView<FMassZoneGraphShortPathFragment> ShortPathList =
                Context.GetFragmentView<FMassZoneGraphShortPathFragment>();
            const TConstArrayView<FAgentRadiusFragment> RadiusList =
                Context.GetFragmentView<FAgentRadiusFragment>();
            TArrayView<FMassMoveTargetFragment> MoveTargetList =
                Context.GetMutableFragmentView<FMassMoveTargetFragment>();
            const TConstArrayView<FMassSimulationLODFragment> SimLODList =
                Context.GetFragmentView<FMassSimulationLODFragment>();
            const TConstArrayView<FMassSimulationVariableTickFragment>
                VariableTickList = Context.GetFragmentView<
                    FMassSimulationVariableTickFragment>();
            const bool bChunkTicks =
                FMassSimulationVariableTickChunkFragment::
                    ShouldTickChunkThisFrame(Context);
            const float WorldDeltaSeconds = Context.GetDeltaTimeSeconds();

            for (FMassExecutionContext::FEntityIterator EntityIt =
                     Context.CreateEntityIterator();
                 EntityIt;
                 ++EntityIt)
            {
                const FOpenMassCrowdCentralVisualOwnerFragment& Owner =
                    OwnerList[EntityIt];
                AOpenMassCrowdSpawner* Spawner = Owner.Spawner.Get();
                if (!IsValid(Spawner) ||
                    Owner.EntityIndex == INDEX_NONE ||
                    Spawner->NetworkMode !=
                        EOpenMassCrowdNetworkMode::CentralCertifiedCache)
                {
                    // Local30 intentionally carries an empty owner fragment.
                    continue;
                }

                const FMassEntityHandle Entity = Context.GetEntity(EntityIt);
                if (!Spawner->SpawnedEntities.IsValidIndex(Owner.EntityIndex) ||
                    Spawner->SpawnedEntities[Owner.EntityIndex] != Entity)
                {
                    continue;
                }

                const FMassZoneGraphLaneLocationFragment& Lane =
                    LaneList[EntityIt];
                const FMassZoneGraphShortPathFragment& ShortPath =
                    ShortPathList[EntityIt];
                FMassMoveTargetFragment& MoveTarget =
                    MoveTargetList[EntityIt];
                const int32 LaneIndex = Lane.LaneHandle.Index;
                if (!Spawner->RuntimeLaneHandles.IsValidIndex(LaneIndex) ||
                    !Spawner->RuntimeCentralPhysicalTrackIndices.IsValidIndex(
                        LaneIndex) ||
                    !Spawner->RuntimeCentralSameDirectionPhysicalLaneIndices.
                        IsValidIndex(LaneIndex) ||
                    !Spawner->RuntimeCentralOpposingPhysicalLaneIndices.
                        IsValidIndex(LaneIndex) ||
                    !Spawner->RuntimeCentralLaneFromNodeIds.IsValidIndex(
                        LaneIndex) ||
                    !Spawner->RuntimeCentralLaneToNodeIds.IsValidIndex(
                        LaneIndex) ||
                    !FMath::IsFinite(Lane.DistanceAlongLane) ||
                    !FMath::IsFinite(RadiusList[EntityIt].Radius) ||
                    RadiusList[EntityIt].Radius <= 0.0f)
                {
                    continue;
                }

                FCentralSpacingRecord& Record =
                    Records.AddDefaulted_GetRef();
                Record.Entity = Entity;
                Record.Spawner = Spawner;
                Record.EntityIndex = Owner.EntityIndex;
                Record.PresentationBandIndex =
                    (FMath::Max(Owner.EntityIndex, 0) * 3) % 7;
                Record.LaneIndex = LaneIndex;
                Record.PhysicalTrackIndex =
                    Spawner->RuntimeCentralPhysicalTrackIndices[LaneIndex];
                Record.FromNodeId =
                    Spawner->RuntimeCentralLaneFromNodeIds[LaneIndex];
                Record.ToNodeId =
                    Spawner->RuntimeCentralLaneToNodeIds[LaneIndex];
                Record.Position = TransformList[EntityIt].GetTransform().GetLocation();
                Record.ProgressCm = Lane.DistanceAlongLane;
                Record.LaneLengthCm = Lane.LaneLength;
                Record.ProgressFraction = Lane.LaneLength > KINDA_SMALL_NUMBER
                    ? FMath::Clamp(
                        Lane.DistanceAlongLane / Lane.LaneLength,
                        0.0f,
                        1.0f)
                    : 0.0f;
                Record.DistanceToNodeCm = Lane.LaneLength > KINDA_SMALL_NUMBER
                    ? FMath::Max(
                        0.0f,
                        Lane.LaneLength - Lane.DistanceAlongLane)
                    : TNumericLimits<float>::Max();
                Record.RadiusCm = RadiusList[EntityIt].Radius;
                Record.CruiseSpeedCmPerSecond =
                    Spawner->GetCentralCruiseSpeedCmPerSecond(
                        Owner.EntityIndex);
                Record.TickDeltaSeconds = VariableTickList.IsEmpty()
                    ? WorldDeltaSeconds
                    : VariableTickList[EntityIt].DeltaTime;
                Record.bActiveMove =
                    MoveTarget.GetCurrentAction() ==
                        EMassMovementAction::Move &&
                    ShortPath.NumPoints >= 2 &&
                    !ShortPath.IsDone();

                // Central deliberately replaces steering's lateral result with
                // the exact certified lane transform after movement.  The
                // stock steering processor can therefore report "falling
                // behind" even though the pedestrian is exactly on the
                // authoritative ground track.  Leaving that diagnostic bit
                // set makes PathFollow stop advancing the lane distance; path
                // refreshes then keep re-activating a valid action at the same
                // point forever.  Clear only this stale steering diagnostic
                // before PathFollow.  The 55 cm headway cap and realised
                // certified rollback below remain the movement authorities.
                if (Record.bActiveMove && MoveTarget.bSteeringFallingBehind)
                {
                    MoveTarget.bSteeringFallingBehind = false;
                }

                const bool bHasSteering = SimLODList.IsEmpty() ||
                    SimLODList[EntityIt].LOD != EMassLOD::Off;
                Record.bWillAdvance = Record.bActiveMove &&
                    bChunkTicks &&
                    (!bHasSteering ||
                     !MoveTarget.bSteeringFallingBehind) &&
                    FMath::IsFinite(Record.TickDeltaSeconds) &&
                    Record.TickDeltaSeconds > 0.0f;
                Record.TargetSpeedCmPerSecond = Record.bActiveMove
                    ? Record.CruiseSpeedCmPerSecond
                    : 0.0f;
                if (Record.bActiveMove &&
                    ShortPath.NextLaneHandle.IsValid() &&
                    ShortPath.NextExitLinkType ==
                        EZoneLaneLinkType::Outgoing &&
                    ShortPath.NumPoints >= 2)
                {
                    const int32 NextLaneIndex =
                        ShortPath.NextLaneHandle.Index;
                    if (Spawner->RuntimeCentralPhysicalTrackIndices.IsValidIndex(
                            NextLaneIndex) &&
                        Spawner->RuntimeCentralSameDirectionPhysicalLaneIndices.
                            IsValidIndex(NextLaneIndex) &&
                        Spawner->RuntimeCentralOpposingPhysicalLaneIndices.
                            IsValidIndex(NextLaneIndex) &&
                        Spawner->RuntimeCentralLaneFromNodeIds.IsValidIndex(
                            NextLaneIndex) &&
                        Spawner->RuntimeCentralLaneFromNodeIds[NextLaneIndex] ==
                            Record.ToNodeId)
                    {
                        Record.NextLaneIndex = NextLaneIndex;
                        Record.NextPhysicalTrackIndex =
                            Spawner->RuntimeCentralPhysicalTrackIndices[
                                NextLaneIndex];
                        Record.TransitionNodeId = Record.ToNodeId;
                        const int32 LastPointIndex =
                            ShortPath.NumPoints - 1;
                        Record.DistanceToTransitionCm = FMath::Max(
                            0.0f,
                            ShortPath.Points[LastPointIndex].Distance.Get() -
                                ShortPath.ProgressDistance);
                        Record.bHasOutgoingTransition = true;
                    }
                }

                Record.PlannedLaneIndices.Add(Record.LaneIndex);
                if (Spawner->EntityRouteStates.IsValidIndex(
                        Record.EntityIndex))
                {
                    const AOpenMassCrowdSpawner::FEntityRouteState& RouteState =
                        Spawner->EntityRouteStates[Record.EntityIndex];
                    int32 CurrentRouteIndex = INDEX_NONE;
                    const int32 SearchStart = FMath::Clamp(
                        RouteState.CurrentPathIndex,
                        0,
                        FMath::Max(RouteState.LanePath.Num() - 1, 0));
                    for (int32 PathIndex = SearchStart;
                         PathIndex < RouteState.LanePath.Num();
                         ++PathIndex)
                    {
                        if (RouteState.LanePath[PathIndex].Index ==
                            Record.LaneIndex)
                        {
                            CurrentRouteIndex = PathIndex;
                            break;
                        }
                    }
                    if (CurrentRouteIndex == INDEX_NONE)
                    {
                        for (int32 PathIndex = 0;
                             PathIndex < SearchStart;
                             ++PathIndex)
                        {
                            if (RouteState.LanePath[PathIndex].Index ==
                                Record.LaneIndex)
                            {
                                CurrentRouteIndex = PathIndex;
                                break;
                            }
                        }
                    }
                    if (CurrentRouteIndex != INDEX_NONE)
                    {
                        for (int32 PathIndex = CurrentRouteIndex + 1;
                             PathIndex < RouteState.LanePath.Num();
                             ++PathIndex)
                        {
                            Record.PlannedLaneIndices.Add(
                                RouteState.LanePath[PathIndex].Index);
                        }
                    }
                }
                if (Record.PlannedLaneIndices.Num() == 1 &&
                    Record.bHasOutgoingTransition)
                {
                    Record.PlannedLaneIndices.Add(Record.NextLaneIndex);
                }
            }
        });

    // One global snapshot makes the result independent of archetype/chunk and
    // batched-admission order. Direct whole-track adjacency is consulted below
    // instead of relying on a transitive alias union: a third recovery lane can
    // never hide the true nearest leader of a conflicting pair.
    Records.Sort(
        [](const FCentralSpacingRecord& A,
           const FCentralSpacingRecord& B)
        {
            if (A.Spawner != B.Spawner)
            {
                return A.Spawner->GetUniqueID() < B.Spawner->GetUniqueID();
            }
            if (A.ProgressFraction != B.ProgressFraction)
            {
                return A.ProgressFraction > B.ProgressFraction;
            }
            return A.EntityIndex < B.EntityIndex;
        });

    constexpr float CentralHeadwaySafetyGapCm = 1.0f;
    // Node/merge/cross reservations implement the gate's severe-overlap
    // contract. Same-direction body headway remains radius + radius + 1 cm.
    constexpr float CentralHardConflictClearanceCm = 20.0f;
    // LaneLocation uses 10 cm fixed-point distances while ShortPath advances
    // in its own 1 cm distance domain.  Reserve one complete LaneLocation
    // quantum so converting the accepted advance cannot cross the 55 cm body
    // boundary in the same frame.
    constexpr float CentralLaneDistanceQuantizationGuardCm = 10.01f;
    constexpr float CentralPathEndpointMarginCm = 0.01f;
    // The certified local-conflict table already covers every cross-lane
    // sample pair below the 20 cm hard gate, including shared nodes, merges,
    // crossings, and destination entrances.  Running the older node and
    // entrance arbiters before that table creates nested ownership: an entity
    // can own a node while waiting for a local interval owned by another
    // entity, and vice versa.  Keep the legacy code available for local
    // diagnostics, but Central uses one all-or-none resource authority.
    constexpr bool bUseLegacyCentralNodeAndEntranceReservations = false;
    const auto AreSameDirectionPhysicalLanes = [](
        const FCentralSpacingRecord& A,
        const FCentralSpacingRecord& B)
    {
        return A.Spawner == B.Spawner &&
            (A.LaneIndex == B.LaneIndex ||
             (A.Spawner->RuntimeCentralSameDirectionPhysicalLaneIndices.
                      IsValidIndex(A.LaneIndex) &&
              A.Spawner->RuntimeCentralSameDirectionPhysicalLaneIndices[
                  A.LaneIndex].Contains(B.LaneIndex)));
    };
    for (int32 RecordIndex = 1; RecordIndex < Records.Num(); ++RecordIndex)
    {
        FCentralSpacingRecord& Follower = Records[RecordIndex];
        if (!Follower.bActiveMove)
        {
            continue;
        }

        int32 LeaderRecordIndex = INDEX_NONE;
        float NearestLeaderDistanceCm = TNumericLimits<float>::Max();
        for (int32 CandidateIndex = 0;
             CandidateIndex < RecordIndex;
             ++CandidateIndex)
        {
            const FCentralSpacingRecord& Candidate =
                Records[CandidateIndex];
            if (!AreSameDirectionPhysicalLanes(Follower, Candidate))
            {
                continue;
            }
            // Progress determines which entity is ahead; the certified 3D
            // positions determine the real body clearance. Arc-length
            // subtraction can overstate clearance on a curved lane.
            const float CandidateDistanceCm = FVector::Distance(
                Candidate.Position,
                Follower.Position);
            if (CandidateDistanceCm >= 0.0f &&
                CandidateDistanceCm < NearestLeaderDistanceCm)
            {
                LeaderRecordIndex = CandidateIndex;
                NearestLeaderDistanceCm = CandidateDistanceCm;
            }
        }
        if (LeaderRecordIndex == INDEX_NONE)
        {
            continue;
        }
        const FCentralSpacingRecord& Leader = Records[LeaderRecordIndex];

        const float RequiredCenterDistanceCm =
            Leader.RadiusCm + Follower.RadiusCm +
            CentralHeadwaySafetyGapCm;
        const bool bSameRuntimeLane =
            Follower.LaneIndex == Leader.LaneIndex;
        const bool bSamePresentationBand =
            Follower.PresentationBandIndex ==
                Leader.PresentationBandIndex;
        // Presentation bands deliberately separate render positions sideways,
        // but two identities on the same authoritative lane still share one
        // topological entrance.  Use the smaller of realised 3D clearance and
        // certified longitudinal progress there, otherwise several banded
        // pedestrians can arrive at exactly the same lane endpoint and block
        // the transition together.
        const float CurrentCenterDistanceCm =
            bSameRuntimeLane && bSamePresentationBand
            ? FMath::Min(
                NearestLeaderDistanceCm,
                FMath::Abs(
                    Leader.ProgressCm - Follower.ProgressCm))
            : NearestLeaderDistanceCm;
        if (CurrentCenterDistanceCm <= RequiredCenterDistanceCm)
        {
            // A pre-existing too-small gap must open; matching the leader here
            // would merely preserve it forever.
            Follower.TargetSpeedCmPerSecond = 0.0f;
            continue;
        }
        if (!Follower.bWillAdvance)
        {
            continue;
        }

        // For near-duplicate but non-identical lanes, triangle inequality only
        // guarantees the follower's own advance. Ignore leader advance there;
        // this is conservative and cannot manufacture clearance.
        const float LeaderAdvanceCm =
            bSameRuntimeLane && Leader.bWillAdvance
            ? Leader.TargetSpeedCmPerSecond * Leader.TickDeltaSeconds
            : 0.0f;
        const float MaximumFollowerAdvanceCm = FMath::Max(
            0.0f,
            CurrentCenterDistanceCm - RequiredCenterDistanceCm +
                LeaderAdvanceCm -
                CentralLaneDistanceQuantizationGuardCm);
        const float MaximumFollowerSpeedCmPerSecond =
            MaximumFollowerAdvanceCm /
            FMath::Max(Follower.TickDeltaSeconds, 0.001f);
        // FMassInt16Real rounds to the nearest cm/s.  Floor the cap so the
        // encoded speed can never round above the reservation bound.
        Follower.TargetSpeedCmPerSecond = FMath::Min(
            Follower.CruiseSpeedCmPerSecond,
            FMath::FloorToFloat(MaximumFollowerSpeedCmPerSecond));
    }

    // Same-lane headway above only sees entities that are already on the same
    // physical lane.  PathFollow can switch an entrant to NextLaneHandle in
    // the same tick, so also reserve the first body length of that destination
    // lane.  Without this entry guard an occupant at progress 0 and an entrant
    // crossing the endpoint can be written only a few centimetres apart before
    // next frame's ordinary headway pass can observe them.
    for (FCentralSpacingRecord& Entrant : Records)
    {
        if (!Entrant.bWillAdvance ||
            !Entrant.bHasOutgoingTransition ||
            Entrant.TargetSpeedCmPerSecond <= 0.0f ||
            !Entrant.Spawner->RuntimeCentralSameDirectionPhysicalLaneIndices.
                IsValidIndex(Entrant.NextLaneIndex))
        {
            continue;
        }
        const float PredictedAdvanceCm =
            Entrant.TargetSpeedCmPerSecond * Entrant.TickDeltaSeconds;
        if (PredictedAdvanceCm <
            Entrant.DistanceToTransitionCm - CentralPathEndpointMarginCm)
        {
            continue;
        }

        const TArray<int32>& SameDirectionDestinationLanes =
            Entrant.Spawner->
                RuntimeCentralSameDirectionPhysicalLaneIndices[
                    Entrant.NextLaneIndex];
        bool bDestinationStartOccupied = false;
        for (const FCentralSpacingRecord& Occupant : Records)
        {
            if (Occupant.Spawner != Entrant.Spawner ||
                Occupant.Entity == Entrant.Entity ||
                (Occupant.LaneIndex != Entrant.NextLaneIndex &&
                 !SameDirectionDestinationLanes.Contains(
                     Occupant.LaneIndex)))
            {
                continue;
            }
            const float RequiredEntryClearanceCm =
                Entrant.RadiusCm + Occupant.RadiusCm +
                CentralHeadwaySafetyGapCm +
                CentralLaneDistanceQuantizationGuardCm;
            if (Occupant.ProgressCm < RequiredEntryClearanceCm)
            {
                bDestinationStartOccupied = true;
                break;
            }
        }
        if (bDestinationStartOccupied)
        {
            Entrant.bDestinationStartOccupied = true;
            const float MaximumAdvanceBeforeTransitionCm = FMath::Max(
                0.0f,
                Entrant.DistanceToTransitionCm -
                    CentralPathEndpointMarginCm);
            Entrant.TargetSpeedCmPerSecond = FMath::Min(
                Entrant.TargetSpeedCmPerSecond,
                FMath::FloorToFloat(
                    MaximumAdvanceBeforeTransitionCm /
                    FMath::Max(Entrant.TickDeltaSeconds, 0.001f)));
        }
    }

    // The 55 cm whole-track opposing relation is a spawn/capacity contract, not
    // a runtime mutex. Serializing an entire declared-reverse track here lets an
    // entity reserve its next lane while another entity on the same approach
    // owns the reverse lane, producing a lane-long circular wait. Runtime hard
    // conflicts are represented below as certified local intervals cut only
    // from samples below 20 cm. Keep the legacy corridor state reset path for
    // compatibility, but never admit a candidate into the whole-track lock.
    TArray<int32> CorridorApproachCandidates;
    for (const FCentralSpacingRecord& Record : Records)
    {
        if (Record.Spawner->RuntimeCentralCorridorWaitSeconds.
                IsValidIndex(Record.EntityIndex) &&
            Record.Spawner->RuntimeCentralCorridorWaitLaneIndices.
                IsValidIndex(Record.EntityIndex))
        {
            // Clear state written by an older hot-reloaded implementation.
            Record.Spawner->RuntimeCentralCorridorWaitSeconds[
                Record.EntityIndex] = 0.0f;
            Record.Spawner->RuntimeCentralCorridorWaitLaneIndices[
                Record.EntityIndex] = INDEX_NONE;
        }
    }
    CorridorApproachCandidates.Sort(
        [&Records](const int32 AIndex, const int32 BIndex)
        {
            const FCentralSpacingRecord& A = Records[AIndex];
            const FCentralSpacingRecord& B = Records[BIndex];
            if (A.Spawner != B.Spawner)
            {
                return A.Spawner->GetUniqueID() < B.Spawner->GetUniqueID();
            }
            const float AWaitSeconds =
                A.Spawner->RuntimeCentralCorridorWaitSeconds.IsValidIndex(
                    A.EntityIndex)
                ? A.Spawner->RuntimeCentralCorridorWaitSeconds[A.EntityIndex]
                : 0.0f;
            const float BWaitSeconds =
                B.Spawner->RuntimeCentralCorridorWaitSeconds.IsValidIndex(
                    B.EntityIndex)
                ? B.Spawner->RuntimeCentralCorridorWaitSeconds[B.EntityIndex]
                : 0.0f;
            if (!FMath::IsNearlyEqual(AWaitSeconds, BWaitSeconds))
            {
                return AWaitSeconds > BWaitSeconds;
            }
            const float AConflictZoneCm = A.RadiusCm * 2.0f + 1.0f;
            const float BConflictZoneCm = B.RadiusCm * 2.0f + 1.0f;
            const float AArrivalSeconds = FMath::Max(
                0.0f,
                A.DistanceToNodeCm - AConflictZoneCm) /
                FMath::Max(A.TargetSpeedCmPerSecond, 1.0f);
            const float BArrivalSeconds = FMath::Max(
                0.0f,
                B.DistanceToNodeCm - BConflictZoneCm) /
                FMath::Max(B.TargetSpeedCmPerSecond, 1.0f);
            if (AArrivalSeconds != BArrivalSeconds)
            {
                return AArrivalSeconds < BArrivalSeconds;
            }
            return A.EntityIndex < B.EntityIndex;
        });

    TMap<AOpenMassCrowdSpawner*, TSet<int32>> DrainBlockedEntryLanes;
    for (const int32 WaiterIndex : CorridorApproachCandidates)
    {
        const FCentralSpacingRecord& Waiter = Records[WaiterIndex];
        const TArray<int32>& OpposingLanes =
            Waiter.Spawner->RuntimeCentralOpposingPhysicalLaneIndices[
                Waiter.NextLaneIndex];
        bool bOppositeOccupied = false;
        for (const FCentralSpacingRecord& Occupant : Records)
        {
            if (Occupant.Spawner == Waiter.Spawner &&
                Occupant.Entity != Waiter.Entity &&
                OpposingLanes.Contains(Occupant.LaneIndex))
            {
                bOppositeOccupied = true;
                break;
            }
        }
        if (bOppositeOccupied)
        {
            // Block every direct lane on the occupied side, not merely the
            // exact lane currently containing an entity. This drains semantic
            // same-side duplicates as one physical direction without unioning
            // their geometry for collision tests.
            TSet<int32>& BlockedLanes =
                DrainBlockedEntryLanes.FindOrAdd(Waiter.Spawner);
            for (const int32 OpposingLaneIndex : OpposingLanes)
            {
                BlockedLanes.Add(OpposingLaneIndex);
            }
        }
    }

    TMap<AOpenMassCrowdSpawner*, TArray<int32>> GrantedEntryLanes;
    TSet<FMassEntityHandle> CorridorHeldEntities;
    TSet<FMassEntityHandle> CorridorOwningEntities;
    for (const int32 CandidateIndex : CorridorApproachCandidates)
    {
        FCentralSpacingRecord& Candidate = Records[CandidateIndex];
        const TArray<int32>& OpposingLanes =
            Candidate.Spawner->RuntimeCentralOpposingPhysicalLaneIndices[
                Candidate.NextLaneIndex];
        bool bDenyEntry = false;
        for (const FCentralSpacingRecord& Occupant : Records)
        {
            if (Occupant.Spawner == Candidate.Spawner &&
                Occupant.Entity != Candidate.Entity &&
                OpposingLanes.Contains(Occupant.LaneIndex))
            {
                bDenyEntry = true;
                break;
            }
        }
        if (!bDenyEntry)
        {
            if (const TSet<int32>* BlockedLanes =
                    DrainBlockedEntryLanes.Find(Candidate.Spawner))
            {
                bDenyEntry = BlockedLanes->Contains(
                    Candidate.NextLaneIndex);
            }
        }
        if (!bDenyEntry)
        {
            if (const TArray<int32>* GrantedLanes =
                    GrantedEntryLanes.Find(Candidate.Spawner))
            {
                for (const int32 GrantedLaneIndex : *GrantedLanes)
                {
                    if (OpposingLanes.Contains(GrantedLaneIndex))
                    {
                        bDenyEntry = true;
                        break;
                    }
                }
            }
        }

        if (!bDenyEntry)
        {
            GrantedEntryLanes.FindOrAdd(Candidate.Spawner).Add(
                Candidate.NextLaneIndex);
            CorridorOwningEntities.Add(Candidate.Entity);
            continue;
        }

        const float ConflictZoneCm =
            Candidate.RadiusCm * 2.0f + CentralHeadwaySafetyGapCm;
        float MaximumAdvanceBeforeCorridorCm = FMath::Max(
            0.0f,
            Candidate.DistanceToNodeCm - ConflictZoneCm -
                CentralPathEndpointMarginCm);
        // PathFollow decides the lane switch from ShortPath.ProgressDistance,
        // not LaneLocation.DistanceAlongLane.  A denied entry must therefore
        // remain strictly before the endpoint in that same coordinate domain.
        MaximumAdvanceBeforeCorridorCm = FMath::Min(
            MaximumAdvanceBeforeCorridorCm,
            FMath::Max(
                0.0f,
                Candidate.DistanceToTransitionCm -
                    CentralPathEndpointMarginCm));
        Candidate.TargetSpeedCmPerSecond = FMath::Min(
            Candidate.TargetSpeedCmPerSecond,
            FMath::FloorToFloat(
                MaximumAdvanceBeforeCorridorCm /
                FMath::Max(Candidate.TickDeltaSeconds, 0.001f)));
        if (!CorridorHeldEntities.Contains(Candidate.Entity))
        {
            CorridorHeldEntities.Add(Candidate.Entity);
            if (Candidate.Spawner->RuntimeCentralCorridorWaitSeconds.
                    IsValidIndex(Candidate.EntityIndex))
            {
                float& WaitSeconds =
                    Candidate.Spawner->RuntimeCentralCorridorWaitSeconds[
                        Candidate.EntityIndex];
                WaitSeconds = FMath::Min(
                    3600.0f,
                    WaitSeconds + FMath::Max(
                        Candidate.TickDeltaSeconds,
                        0.0f));
            }
            Candidate.Spawner->CentralCorridorDirectionHoldCount =
                Candidate.Spawner->CentralCorridorDirectionHoldCount >=
                        MAX_int32
                    ? MAX_int32
                    : Candidate.Spawner->
                          CentralCorridorDirectionHoldCount + 1;
        }
    }

    // Treat the hard-conflict distance on both sides of a certified node as one
    // conflict zone.  An entity already leaving the node owns it first; if the
    // zone is empty, the closest incoming entity (stable entity index tie) gets
    // right-of-way. Other incoming entities are capped before they enter the
    // 20 cm zone, rather than being allowed to reach the endpoint and overlap
    // a pedestrian who has only just left it.
    struct FCentralNodeReservationGroup
    {
        AOpenMassCrowdSpawner* Spawner = nullptr;
        FName NodeId = NAME_None;
        TArray<int32> OutgoingRecordIndices;
        TArray<int32> IncomingRecordIndices;
    };
    TArray<FCentralNodeReservationGroup> NodeGroups;
    TMap<AOpenMassCrowdSpawner*, TMap<FName, int32>> NodeGroupIndices;
    const auto FindOrAddNodeGroup =
        [&NodeGroups, &NodeGroupIndices](
            AOpenMassCrowdSpawner* Spawner,
            const FName NodeId) -> FCentralNodeReservationGroup&
    {
        TMap<FName, int32>& SpawnerGroups =
            NodeGroupIndices.FindOrAdd(Spawner);
        if (const int32* ExistingIndex = SpawnerGroups.Find(NodeId))
        {
            return NodeGroups[*ExistingIndex];
        }
        const int32 NewIndex = NodeGroups.AddDefaulted();
        SpawnerGroups.Add(NodeId, NewIndex);
        NodeGroups[NewIndex].Spawner = Spawner;
        NodeGroups[NewIndex].NodeId = NodeId;
        return NodeGroups[NewIndex];
    };

    for (int32 RecordIndex = 0;
         RecordIndex < Records.Num();
         ++RecordIndex)
    {
        const FCentralSpacingRecord& Record = Records[RecordIndex];
        const float OwnConflictDistanceCm =
            CentralHardConflictClearanceCm;
        if (bUseLegacyCentralNodeAndEntranceReservations &&
            !Record.FromNodeId.IsNone() &&
            Record.ProgressCm < OwnConflictDistanceCm)
        {
            FindOrAddNodeGroup(Record.Spawner, Record.FromNodeId)
                .OutgoingRecordIndices.Add(RecordIndex);
        }

        const float PredictedAdvanceCm = Record.bWillAdvance
            ? Record.TargetSpeedCmPerSecond * Record.TickDeltaSeconds
            : 0.0f;
        if (bUseLegacyCentralNodeAndEntranceReservations &&
            !Record.ToNodeId.IsNone() &&
            (Record.DistanceToNodeCm < OwnConflictDistanceCm ||
             PredictedAdvanceCm >
                 Record.DistanceToNodeCm - OwnConflictDistanceCm))
        {
            FindOrAddNodeGroup(Record.Spawner, Record.ToNodeId)
                .IncomingRecordIndices.Add(RecordIndex);
        }
    }

    TMap<TWeakObjectPtr<AOpenMassCrowdSpawner>, TSet<FName>>
        ActiveNodeIdsBySpawner;
    for (const FCentralNodeReservationGroup& Group : NodeGroups)
    {
        ActiveNodeIdsBySpawner.FindOrAdd(Group.Spawner).Add(Group.NodeId);
    }

    TSet<FMassEntityHandle> NodeHeldEntities;
    TSet<FMassEntityHandle> NodeOwningEntities;
    for (FCentralNodeReservationGroup& Group : NodeGroups)
    {
        int32 OwnerRecordIndex = INDEX_NONE;
        TMap<FName, FMassEntityHandle>& PersistentOwners =
            RuntimeCentralNodeOwners.FindOrAdd(Group.Spawner);
        const FMassEntityHandle PreviousOwner =
            PersistentOwners.FindRef(Group.NodeId);

        // Preserve the lease while its owner crosses from the incoming side to
        // the outgoing side. This prevents frame-to-frame speed changes from
        // swapping two predicted arrivals before either pedestrian clears.
        for (const int32 RecordIndex : Group.OutgoingRecordIndices)
        {
            if (Records[RecordIndex].Entity == PreviousOwner)
            {
                OwnerRecordIndex = RecordIndex;
                break;
            }
        }

        // Existing outgoing occupants always own the zone. If an old invalid
        // frame left more than one there, prefer the one closest to clearing it;
        // stable EntityIndex breaks exact ties.
        if (OwnerRecordIndex == INDEX_NONE)
        {
            for (const int32 RecordIndex : Group.OutgoingRecordIndices)
            {
                const FCentralSpacingRecord& Candidate = Records[RecordIndex];
                if (OwnerRecordIndex == INDEX_NONE ||
                    Candidate.ProgressCm >
                        Records[OwnerRecordIndex].ProgressCm ||
                    (Candidate.ProgressCm ==
                         Records[OwnerRecordIndex].ProgressCm &&
                     Candidate.EntityIndex <
                         Records[OwnerRecordIndex].EntityIndex))
                {
                    OwnerRecordIndex = RecordIndex;
                }
            }
        }

        if (OwnerRecordIndex == INDEX_NONE)
        {
            for (const int32 RecordIndex : Group.IncomingRecordIndices)
            {
                const FCentralSpacingRecord& Candidate = Records[RecordIndex];
                if (Candidate.Entity == PreviousOwner &&
                    !CorridorHeldEntities.Contains(Candidate.Entity))
                {
                    OwnerRecordIndex = RecordIndex;
                    break;
                }
            }
        }

        if (OwnerRecordIndex == INDEX_NONE)
        {
            // A pedestrian already inside the incoming side wins over one
            // merely predicted to enter this frame.
            for (const int32 RecordIndex : Group.IncomingRecordIndices)
            {
                const FCentralSpacingRecord& Candidate = Records[RecordIndex];
                if (CorridorHeldEntities.Contains(Candidate.Entity))
                {
                    // The opposing corridor must drain first.  Letting its
                    // held entrant own this node would stop the draining side
                    // and create a circular wait.
                    continue;
                }
                const float OwnConflictDistanceCm =
                    CentralHardConflictClearanceCm;
                if (Candidate.DistanceToNodeCm >= OwnConflictDistanceCm)
                {
                    continue;
                }
                if (OwnerRecordIndex == INDEX_NONE ||
                    Candidate.DistanceToNodeCm <
                        Records[OwnerRecordIndex].DistanceToNodeCm ||
                    (Candidate.DistanceToNodeCm ==
                         Records[OwnerRecordIndex].DistanceToNodeCm &&
                     Candidate.EntityIndex <
                         Records[OwnerRecordIndex].EntityIndex))
                {
                    OwnerRecordIndex = RecordIndex;
                }
            }
        }
        if (OwnerRecordIndex == INDEX_NONE)
        {
            // The zone is empty: earliest predicted arrival reserves it.
            for (const int32 RecordIndex : Group.IncomingRecordIndices)
            {
                const FCentralSpacingRecord& Candidate = Records[RecordIndex];
                if (CorridorHeldEntities.Contains(Candidate.Entity))
                {
                    continue;
                }
                const float OwnConflictDistanceCm =
                    CentralHardConflictClearanceCm;
                const float ArrivalSeconds = FMath::Max(
                    0.0f,
                    Candidate.DistanceToNodeCm -
                        OwnConflictDistanceCm) /
                    FMath::Max(
                        Candidate.TargetSpeedCmPerSecond,
                        1.0f);
                if (OwnerRecordIndex == INDEX_NONE)
                {
                    OwnerRecordIndex = RecordIndex;
                    continue;
                }
                const FCentralSpacingRecord& CurrentOwner =
                    Records[OwnerRecordIndex];
                const float OwnerConflictDistanceCm =
                    CentralHardConflictClearanceCm;
                const float OwnerArrivalSeconds = FMath::Max(
                    0.0f,
                    CurrentOwner.DistanceToNodeCm -
                        OwnerConflictDistanceCm) /
                    FMath::Max(
                        CurrentOwner.TargetSpeedCmPerSecond,
                        1.0f);
                if (ArrivalSeconds < OwnerArrivalSeconds ||
                    (ArrivalSeconds == OwnerArrivalSeconds &&
                     Candidate.EntityIndex < CurrentOwner.EntityIndex))
                {
                    OwnerRecordIndex = RecordIndex;
                }
            }
        }
        if (OwnerRecordIndex == INDEX_NONE)
        {
            PersistentOwners.Remove(Group.NodeId);
            continue;
        }

        const FCentralSpacingRecord& Owner = Records[OwnerRecordIndex];
        PersistentOwners.Add(Group.NodeId, Owner.Entity);
        NodeOwningEntities.Add(Owner.Entity);
        const auto RecordNodeHold = [&NodeHeldEntities](
            FCentralSpacingRecord& Record)
        {
            if (!NodeHeldEntities.Contains(Record.Entity))
            {
                NodeHeldEntities.Add(Record.Entity);
                Record.Spawner->CentralNodeReservationHoldCount =
                    Record.Spawner->CentralNodeReservationHoldCount >=
                            MAX_int32
                        ? MAX_int32
                        : Record.Spawner->CentralNodeReservationHoldCount + 1;
            }
        };

        for (const int32 RecordIndex : Group.OutgoingRecordIndices)
        {
            if (RecordIndex == OwnerRecordIndex)
            {
                continue;
            }
            FCentralSpacingRecord& NonOwner = Records[RecordIndex];
            NonOwner.TargetSpeedCmPerSecond = 0.0f;
            RecordNodeHold(NonOwner);
        }
        for (const int32 RecordIndex : Group.IncomingRecordIndices)
        {
            if (RecordIndex == OwnerRecordIndex)
            {
                continue;
            }
            FCentralSpacingRecord& NonOwner = Records[RecordIndex];
            const float RequiredCenterDistanceCm =
                CentralHardConflictClearanceCm;
            float MaximumAdvanceBeforeNodeZoneCm = FMath::Max(
                0.0f,
                NonOwner.DistanceToNodeCm -
                    RequiredCenterDistanceCm -
                    CentralPathEndpointMarginCm);
            // Certified topology nodes can have distinct surface endpoints.
            // Bound this frame by the actual owner transform as well as by
            // distance-to-node, using one lane-distance quantum as the guard.
            const float ActualOwnerDistanceCm = FVector::Distance(
                NonOwner.Position,
                Owner.Position);
            MaximumAdvanceBeforeNodeZoneCm = FMath::Min(
                MaximumAdvanceBeforeNodeZoneCm,
                FMath::Max(
                    0.0f,
                    ActualOwnerDistanceCm -
                        RequiredCenterDistanceCm -
                        CentralLaneDistanceQuantizationGuardCm));
            if (NonOwner.bHasOutgoingTransition)
            {
                MaximumAdvanceBeforeNodeZoneCm = FMath::Min(
                    MaximumAdvanceBeforeNodeZoneCm,
                    FMath::Max(
                        0.0f,
                        NonOwner.DistanceToTransitionCm -
                            CentralPathEndpointMarginCm));
            }
            const float MaximumSpeedBeforeNodeZoneCmPerSecond =
                FMath::FloorToFloat(
                    MaximumAdvanceBeforeNodeZoneCm /
                    FMath::Max(
                        NonOwner.TickDeltaSeconds,
                        0.001f));
            NonOwner.TargetSpeedCmPerSecond = FMath::Min(
                NonOwner.TargetSpeedCmPerSecond,
                MaximumSpeedBeforeNodeZoneCmPerSecond);
            RecordNodeHold(NonOwner);
        }
    }

    // Drop leases only after their owner has left the node resource entirely;
    // do not let an unrelated replan erase right-of-way age mid-conflict.
    for (auto SpawnerIt = RuntimeCentralNodeOwners.CreateIterator();
         SpawnerIt;
         ++SpawnerIt)
    {
        const TSet<FName>* ActiveNodeIds =
            SpawnerIt.Key().IsValid()
            ? ActiveNodeIdsBySpawner.Find(SpawnerIt.Key())
            : nullptr;
        if (!ActiveNodeIds)
        {
            SpawnerIt.RemoveCurrent();
            continue;
        }
        for (auto NodeIt = SpawnerIt.Value().CreateIterator();
             NodeIt;
             ++NodeIt)
        {
            if (!ActiveNodeIds->Contains(NodeIt.Key()))
            {
                NodeIt.RemoveCurrent();
            }
        }
        if (SpawnerIt.Value().IsEmpty())
        {
            SpawnerIt.RemoveCurrent();
        }
    }

    // UE changes LaneHandle to NextLaneHandle and writes progress zero in the
    // same PathFollow call that crosses the path endpoint.  The node zone above
    // guarantees a single node owner; this second layer reserves the physical
    // destination entrance (including near-duplicate lane aliases).
    TArray<int32> TransitionCandidates;
    for (int32 RecordIndex = 0;
         RecordIndex < Records.Num();
         ++RecordIndex)
    {
        const FCentralSpacingRecord& Record = Records[RecordIndex];
        if (bUseLegacyCentralNodeAndEntranceReservations &&
            Record.bWillAdvance &&
            Record.bHasOutgoingTransition &&
            Record.TargetSpeedCmPerSecond > 0.0f &&
            Record.TargetSpeedCmPerSecond * Record.TickDeltaSeconds >
                Record.DistanceToTransitionCm)
        {
            TransitionCandidates.Add(RecordIndex);
        }
    }
    TransitionCandidates.Sort(
        [&Records](const int32 AIndex, const int32 BIndex)
        {
            const FCentralSpacingRecord& A = Records[AIndex];
            const FCentralSpacingRecord& B = Records[BIndex];
            if (A.Spawner != B.Spawner)
            {
                return A.Spawner->GetUniqueID() < B.Spawner->GetUniqueID();
            }
            const float AArrivalSeconds = A.DistanceToTransitionCm /
                FMath::Max(A.TargetSpeedCmPerSecond, 1.0f);
            const float BArrivalSeconds = B.DistanceToTransitionCm /
                FMath::Max(B.TargetSpeedCmPerSecond, 1.0f);
            if (AArrivalSeconds != BArrivalSeconds)
            {
                return AArrivalSeconds < BArrivalSeconds;
            }
            return A.EntityIndex < B.EntityIndex;
        });

    TMap<AOpenMassCrowdSpawner*, TSet<FName>> ReservedNodesBySpawner;
    TMap<AOpenMassCrowdSpawner*, TSet<int32>>
        ReservedEntryLanesBySpawner;
    for (const int32 CandidateIndex : TransitionCandidates)
    {
        FCentralSpacingRecord& Candidate = Records[CandidateIndex];
        TSet<FName>& ReservedNodes =
            ReservedNodesBySpawner.FindOrAdd(Candidate.Spawner);
        TSet<int32>& ReservedEntryLanes =
            ReservedEntryLanesBySpawner.FindOrAdd(Candidate.Spawner);
        const TArray<int32>& SameDirectionEntranceLanes =
            Candidate.Spawner->
                RuntimeCentralSameDirectionPhysicalLaneIndices[
                    Candidate.NextLaneIndex];
        bool bEntranceOccupied =
            ReservedNodes.Contains(Candidate.TransitionNodeId);
        if (!bEntranceOccupied)
        {
            for (const int32 ReservedLaneIndex : ReservedEntryLanes)
            {
                if (ReservedLaneIndex == Candidate.NextLaneIndex ||
                    SameDirectionEntranceLanes.Contains(ReservedLaneIndex))
                {
                    bEntranceOccupied = true;
                    break;
                }
            }
        }

        if (!bEntranceOccupied)
        {
            for (const FCentralSpacingRecord& Occupant : Records)
            {
                if (Occupant.Spawner != Candidate.Spawner ||
                    Occupant.Entity == Candidate.Entity)
                {
                    continue;
                }
                const float RequiredCenterDistanceCm =
                    Candidate.RadiusCm + Occupant.RadiusCm +
                    CentralHeadwaySafetyGapCm;
                const bool bOccupiesPhysicalEntrance =
                    (Occupant.LaneIndex == Candidate.NextLaneIndex ||
                     SameDirectionEntranceLanes.Contains(
                         Occupant.LaneIndex)) &&
                    Occupant.ProgressCm < RequiredCenterDistanceCm;
                const bool bOccupiesNodeExit =
                    Occupant.FromNodeId == Candidate.TransitionNodeId &&
                    Occupant.ProgressCm < RequiredCenterDistanceCm;
                if (bOccupiesPhysicalEntrance || bOccupiesNodeExit)
                {
                    bEntranceOccupied = true;
                    break;
                }
            }
        }

        if (!bEntranceOccupied)
        {
            ReservedNodes.Add(Candidate.TransitionNodeId);
            ReservedEntryLanes.Add(Candidate.NextLaneIndex);
            continue;
        }

        // PathFollow changes lanes only when progress is strictly greater than
        // its last point. Keep a small sub-centimetre margin and floor before
        // FMassInt16Real rounding, so a denied transition cannot slip through.
        const float ConflictZoneCm =
            Candidate.RadiusCm * 2.0f + CentralHeadwaySafetyGapCm;
        float MaximumNonTransitionAdvanceCm = FMath::Max(
            0.0f,
            Candidate.DistanceToNodeCm - ConflictZoneCm -
                CentralPathEndpointMarginCm);
        MaximumNonTransitionAdvanceCm = FMath::Min(
            MaximumNonTransitionAdvanceCm,
            FMath::Max(
                0.0f,
                Candidate.DistanceToTransitionCm -
                    CentralPathEndpointMarginCm));
        const float MaximumNonTransitionSpeedCmPerSecond = FMath::FloorToFloat(
            MaximumNonTransitionAdvanceCm /
            FMath::Max(Candidate.TickDeltaSeconds, 0.001f));
        Candidate.TargetSpeedCmPerSecond = FMath::Min(
            Candidate.TargetSpeedCmPerSecond,
            MaximumNonTransitionSpeedCmPerSecond);
    }

    // Reserve every certified-sample local conflict interval, including
    // crossings and merges whose lanes do not share a topology node. A record
    // requests every interval boundary it can reach this tick; all requests are
    // checked first and then granted together, or the record is stopped before
    // the earliest boundary. A closure that reaches a lane end recursively
    // includes every zero-distance closure on the remaining planned LanePath,
    // until a conflict-free gap, so the contiguous route segment is acquired
    // all-or-none before its first boundary and cannot create hold-and-wait.
    constexpr float CentralLocalConflictReplanSeconds = 1.5f;
    // Busy intersections drain through the stable realised-transform
    // arbitration below; route replacement is not part of that fast path.
    constexpr bool bEnableCentralConflictWaitReplans = false;
    // The interval table remains immutable audit evidence, but executing its
    // predictive mutex path would both recreate tree-component deadlocks and
    // allocate one 29k-entry claim array per moving entity every frame.
    constexpr bool bEnableCentralLocalConflictReservations = false;
    if constexpr (bEnableCentralLocalConflictReservations)
    {
    struct FCentralLocalConflictRequest
    {
        int32 ConflictIndex = INDEX_NONE;
        int32 LaneIndex = INDEX_NONE;
        uint8 SideMask = 0;
        float DistanceToReservationBoundaryCm =
            TNumericLimits<float>::Max();
    };
    struct FCentralLocalConflictCandidate
    {
        int32 RecordIndex = INDEX_NONE;
        TArray<FCentralLocalConflictRequest> Requests;
        float NearestBoundaryCm = TNumericLimits<float>::Max();
        float ExistingWaitSeconds = 0.0f;
        bool bOccupiesLocalConflict = false;
        bool bHoldsAnotherResource = false;
    };
    // The certified transform processor is the final physical authority: it
    // evaluates the realised transforms together and atomically restores the
    // losing entity's transform, lane, short path, and move target whenever a
    // pair would enter the strict <20 cm region.  Keeping this older predictive
    // interval reservation active as well turns the 20 cm-separated opposing
    // certified tracks into a one-dimensional mutex.  On a tree component two
    // pedestrians then cannot pass or turn around and a circular wait is
    // inevitable.  Leave the interval table as audit/diagnostic evidence, but
    // let the realised certified rollback arbitrate physical motion.
    const auto GatherOverlappingLaneConflictClosure = [](
        AOpenMassCrowdSpawner* Spawner,
        const int32 LaneIndex,
        const int32 SeedConflictIndex,
        const float,
        TArray<int32>& OutConflictIndices)
    {
        OutConflictIndices.Reset();
        if (!Spawner->RuntimeCentralLocalConflictClosuresByLane.IsValidIndex(
                LaneIndex))
        {
            return;
        }
        const TArray<int32>* ClosureConflictIndices =
            Spawner->RuntimeCentralLocalConflictClosuresByLane[
                LaneIndex].Find(SeedConflictIndex);
        if (ClosureConflictIndices)
        {
            OutConflictIndices = *ClosureConflictIndices;
        }
    };
    const auto GetLaneConflictInterval = [](
        const AOpenMassCrowdSpawner::FRuntimeCentralLocalConflict& Conflict,
        const int32 LaneIndex,
        float& OutBeginDistanceCm,
        float& OutEndDistanceCm,
        uint8& OutSideMask)
    {
        if (LaneIndex == Conflict.FirstLaneIndex)
        {
            OutBeginDistanceCm = Conflict.FirstBeginDistanceCm;
            OutEndDistanceCm = Conflict.FirstEndDistanceCm;
            OutSideMask = 1;
            return true;
        }
        if (LaneIndex == Conflict.SecondLaneIndex)
        {
            OutBeginDistanceCm = Conflict.SecondBeginDistanceCm;
            OutEndDistanceCm = Conflict.SecondEndDistanceCm;
            OutSideMask = 2;
            return true;
        }
        return false;
    };
    const auto AddClosureClaims = [
        &GetLaneConflictInterval](
            AOpenMassCrowdSpawner* Spawner,
            const int32 LaneIndex,
            const TArray<int32>& ClosureConflictIndices,
            TArray<uint8>& OutClaimSides)
    {
        for (const int32 ClosureConflictIndex : ClosureConflictIndices)
        {
            if (!Spawner->RuntimeCentralLocalConflicts.IsValidIndex(
                    ClosureConflictIndex) ||
                !OutClaimSides.IsValidIndex(ClosureConflictIndex))
            {
                continue;
            }
            float BeginDistanceCm = 0.0f;
            float EndDistanceCm = 0.0f;
            uint8 SideMask = 0;
            if (!GetLaneConflictInterval(
                    Spawner->RuntimeCentralLocalConflicts[
                        ClosureConflictIndex],
                    LaneIndex,
                    BeginDistanceCm,
                    EndDistanceCm,
                    SideMask))
            {
                continue;
            }
            // Side mask 3 is an intentional entity-owned exclusive claim when
            // one planned route traverses both sides of this resource before a
            // conflict-free gap. Arbitration keeps its owner identity, waits
            // for every other claim to drain, then preserves it across frames.
            OutClaimSides[ClosureConflictIndex] |= SideMask;
        }
    };
    const auto DoesClosureReachLaneEnd = [
        &GetLaneConflictInterval](
            AOpenMassCrowdSpawner* Spawner,
            const int32 LaneIndex,
            const TArray<int32>& ClosureConflictIndices,
            const float BodyClearanceCm)
    {
        if (!Spawner->RuntimeCentralLaneLengthsCm.IsValidIndex(LaneIndex))
        {
            return false;
        }
        const float LaneLengthCm =
            Spawner->RuntimeCentralLaneLengthsCm[LaneIndex];
        for (const int32 ClosureConflictIndex : ClosureConflictIndices)
        {
            if (!Spawner->RuntimeCentralLocalConflicts.IsValidIndex(
                    ClosureConflictIndex))
            {
                continue;
            }
            float BeginDistanceCm = 0.0f;
            float EndDistanceCm = 0.0f;
            uint8 SideMask = 0;
            if (GetLaneConflictInterval(
                    Spawner->RuntimeCentralLocalConflicts[
                        ClosureConflictIndex],
                    LaneIndex,
                    BeginDistanceCm,
                    EndDistanceCm,
                    SideMask) &&
                EndDistanceCm + BodyClearanceCm >= LaneLengthCm - 0.01f)
            {
                return true;
            }
        }
        return false;
    };
    const auto AppendRouteEntryClaimChain = [
        &GatherOverlappingLaneConflictClosure,
        &GetLaneConflictInterval,
        &AddClosureClaims,
        &DoesClosureReachLaneEnd](
            const FCentralSpacingRecord& Record,
            const int32 FirstRoutePathIndex,
            const float BodyClearanceCm,
            TArray<uint8>& OutClaimSides)
    {
        for (int32 RoutePathIndex = FirstRoutePathIndex;
             RoutePathIndex < Record.PlannedLaneIndices.Num();
             ++RoutePathIndex)
        {
            const int32 LaneIndex =
                Record.PlannedLaneIndices[RoutePathIndex];
            if (!Record.Spawner->RuntimeCentralLocalConflictIndicesByLane.
                    IsValidIndex(LaneIndex))
            {
                return;
            }
            TArray<int32> EntryClosureConflictIndices;
            for (const int32 SeedConflictIndex :
                 Record.Spawner->RuntimeCentralLocalConflictIndicesByLane[
                     LaneIndex])
            {
                if (!Record.Spawner->RuntimeCentralLocalConflicts.IsValidIndex(
                        SeedConflictIndex))
                {
                    continue;
                }
                float BeginDistanceCm = 0.0f;
                float EndDistanceCm = 0.0f;
                uint8 SideMask = 0;
                if (!GetLaneConflictInterval(
                        Record.Spawner->RuntimeCentralLocalConflicts[
                            SeedConflictIndex],
                        LaneIndex,
                        BeginDistanceCm,
                        EndDistanceCm,
                        SideMask) ||
                    FMath::Max(
                        0.0f,
                        BeginDistanceCm - BodyClearanceCm) > 0.01f)
                {
                    continue;
                }
                TArray<int32> SeedClosureConflictIndices;
                GatherOverlappingLaneConflictClosure(
                    Record.Spawner,
                    LaneIndex,
                    SeedConflictIndex,
                    BodyClearanceCm,
                    SeedClosureConflictIndices);
                for (const int32 ClosureConflictIndex :
                     SeedClosureConflictIndices)
                {
                    EntryClosureConflictIndices.AddUnique(
                        ClosureConflictIndex);
                }
            }
            if (EntryClosureConflictIndices.IsEmpty())
            {
                return;
            }
            EntryClosureConflictIndices.Sort();
            AddClosureClaims(
                Record.Spawner,
                LaneIndex,
                EntryClosureConflictIndices,
                OutClaimSides);
            if (!DoesClosureReachLaneEnd(
                    Record.Spawner,
                    LaneIndex,
                    EntryClosureConflictIndices,
                    BodyClearanceCm))
            {
                return;
            }
        }
    };
    TArray<TArray<uint8>> InitialClaimSidesByRecord;
    InitialClaimSidesByRecord.SetNum(Records.Num());
    TSet<FMassEntityHandle> LocalConflictOccupants;
    for (int32 RecordIndex = 0;
         RecordIndex < Records.Num();
         ++RecordIndex)
    {
        const FCentralSpacingRecord& Record = Records[RecordIndex];
        if (!Record.Spawner->RuntimeCentralLocalConflictIndicesByLane.
                IsValidIndex(Record.LaneIndex))
        {
            continue;
        }
        const float BodyClearanceCm =
            CentralHardConflictClearanceCm;
        TArray<uint8> RecordClaimSides;
        RecordClaimSides.SetNumZeroed(
            Record.Spawner->RuntimeCentralLocalConflicts.Num());
        bool bInsideLocalConflict = false;
        for (const int32 ConflictIndex :
             Record.Spawner->RuntimeCentralLocalConflictIndicesByLane[
                 Record.LaneIndex])
        {
            if (!Record.Spawner->RuntimeCentralLocalConflicts.IsValidIndex(
                    ConflictIndex))
            {
                continue;
            }
            const AOpenMassCrowdSpawner::FRuntimeCentralLocalConflict&
                Conflict = Record.Spawner->RuntimeCentralLocalConflicts[
                    ConflictIndex];
            float BeginDistanceCm = 0.0f;
            float EndDistanceCm = 0.0f;
            uint8 SideMask = 0;
            if (!GetLaneConflictInterval(
                    Conflict,
                    Record.LaneIndex,
                    BeginDistanceCm,
                    EndDistanceCm,
                    SideMask))
            {
                continue;
            }
            const float ExpandedBeginDistanceCm = FMath::Max(
                0.0f,
                BeginDistanceCm - BodyClearanceCm);
            const float ExpandedEndDistanceCm =
                EndDistanceCm + BodyClearanceCm;
            if (Record.ProgressCm >= ExpandedBeginDistanceCm &&
                Record.ProgressCm <= ExpandedEndDistanceCm)
            {
                TArray<int32> ClosureConflictIndices;
                GatherOverlappingLaneConflictClosure(
                    Record.Spawner,
                    Record.LaneIndex,
                    ConflictIndex,
                    BodyClearanceCm,
                    ClosureConflictIndices);
                AddClosureClaims(
                    Record.Spawner,
                    Record.LaneIndex,
                    ClosureConflictIndices,
                    RecordClaimSides);
                if (Record.PlannedLaneIndices.Num() > 1 &&
                    DoesClosureReachLaneEnd(
                        Record.Spawner,
                        Record.LaneIndex,
                        ClosureConflictIndices,
                        BodyClearanceCm))
                {
                    AppendRouteEntryClaimChain(
                        Record,
                        1,
                        BodyClearanceCm,
                        RecordClaimSides);
                }
                bInsideLocalConflict = true;
            }
        }
        if (bInsideLocalConflict)
        {
            InitialClaimSidesByRecord[RecordIndex] =
                MoveTemp(RecordClaimSides);
            LocalConflictOccupants.Add(Record.Entity);
        }
    }

    TArray<FCentralLocalConflictCandidate> LocalConflictCandidates;
    TSet<FMassEntityHandle> LocalConflictCandidateEntities;
    for (int32 RecordIndex = 0;
         RecordIndex < Records.Num();
         ++RecordIndex)
    {
        const FCentralSpacingRecord& Record = Records[RecordIndex];
        if (!Record.bWillAdvance ||
            Record.TargetSpeedCmPerSecond <= 0.0f)
        {
            continue;
        }
        FCentralLocalConflictCandidate Candidate;
        Candidate.RecordIndex = RecordIndex;
        Candidate.bOccupiesLocalConflict =
            LocalConflictOccupants.Contains(Record.Entity);
        Candidate.bHoldsAnotherResource =
            Candidate.bOccupiesLocalConflict ||
            NodeOwningEntities.Contains(Record.Entity) ||
            CorridorOwningEntities.Contains(Record.Entity);
        const float PredictedAdvanceCm =
            Record.TargetSpeedCmPerSecond * Record.TickDeltaSeconds;
        const float BodyClearanceCm =
            CentralHardConflictClearanceCm;
        const auto AddClaimRequests = [
            &Candidate,
            &Record](
                const TArray<uint8>& ClaimSides,
                const float DistanceToBoundaryCm)
        {
            for (int32 ConflictIndex = 0;
                 ConflictIndex < ClaimSides.Num();
                 ++ConflictIndex)
            {
                const uint8 SideMask = ClaimSides[ConflictIndex];
                if (SideMask == 0 ||
                    !Record.Spawner->RuntimeCentralLocalConflicts.IsValidIndex(
                        ConflictIndex))
                {
                    continue;
                }
                FCentralLocalConflictRequest* ExistingRequest =
                    Candidate.Requests.FindByPredicate(
                        [ConflictIndex](
                            const FCentralLocalConflictRequest& Request)
                        {
                            return Request.ConflictIndex == ConflictIndex;
                        });
                if (!ExistingRequest)
                {
                    const AOpenMassCrowdSpawner::FRuntimeCentralLocalConflict&
                        Conflict =
                            Record.Spawner->RuntimeCentralLocalConflicts[
                                ConflictIndex];
                    FCentralLocalConflictRequest& Request =
                        Candidate.Requests.AddDefaulted_GetRef();
                    Request.ConflictIndex = ConflictIndex;
                    Request.LaneIndex = SideMask == 2
                        ? Conflict.SecondLaneIndex
                        : Conflict.FirstLaneIndex;
                    Request.SideMask = SideMask;
                    Request.DistanceToReservationBoundaryCm =
                        DistanceToBoundaryCm;
                }
                else
                {
                    ExistingRequest->SideMask |= SideMask;
                    ExistingRequest->DistanceToReservationBoundaryCm =
                        FMath::Min(
                            ExistingRequest->DistanceToReservationBoundaryCm,
                            DistanceToBoundaryCm);
                }
            }
        };
        if (Candidate.bOccupiesLocalConflict &&
            InitialClaimSidesByRecord.IsValidIndex(RecordIndex))
        {
            // A moving occupant participates in the same deterministic grant
            // pass as every entrant.  Pre-granting both occupants' historic
            // sides makes a head-on pair mutually immutable forever; request
            // the current closure again so exactly one stable-priority owner
            // drains it while the other remains behind the hard boundary.
            AddClaimRequests(
                InitialClaimSidesByRecord[RecordIndex],
                0.0f);
        }
        const auto AddLaneRequests = [
            &Record,
            &GatherOverlappingLaneConflictClosure,
            &GetLaneConflictInterval,
            &AddClosureClaims,
            &DoesClosureReachLaneEnd,
            &AppendRouteEntryClaimChain,
            &AddClaimRequests,
            PredictedAdvanceCm,
            BodyClearanceCm](
                const int32 LaneIndex,
                const float ProgressCm,
                const float BaseDistanceCm,
                const bool bAlreadyOnLane,
                const int32 RoutePathIndex)
        {
            if (!Record.Spawner->RuntimeCentralLocalConflictIndicesByLane.
                    IsValidIndex(LaneIndex))
            {
                return;
            }
            for (const int32 ConflictIndex :
                 Record.Spawner->RuntimeCentralLocalConflictIndicesByLane[
                     LaneIndex])
            {
                if (!Record.Spawner->RuntimeCentralLocalConflicts.IsValidIndex(
                        ConflictIndex))
                {
                    continue;
                }
                const AOpenMassCrowdSpawner::FRuntimeCentralLocalConflict&
                    Conflict =
                        Record.Spawner->RuntimeCentralLocalConflicts[
                            ConflictIndex];
                float BeginDistanceCm = 0.0f;
                float EndDistanceCm = 0.0f;
                uint8 SideMask = 0;
                if (!GetLaneConflictInterval(
                        Conflict,
                        LaneIndex,
                        BeginDistanceCm,
                        EndDistanceCm,
                        SideMask))
                {
                    continue;
                }
                const float ExpandedBeginDistanceCm = FMath::Max(
                    0.0f,
                    BeginDistanceCm - BodyClearanceCm);
                const float ExpandedEndDistanceCm =
                    EndDistanceCm + BodyClearanceCm;
                if (bAlreadyOnLane &&
                    ProgressCm >= ExpandedBeginDistanceCm &&
                    ProgressCm <= ExpandedEndDistanceCm)
                {
                    // Existing occupants own their current side and must keep
                    // draining; treating them as new entrants would stop them
                    // inside the resource as soon as the opposite side waits.
                    continue;
                }
                if (ProgressCm > ExpandedEndDistanceCm)
                {
                    continue;
                }
                const float DistanceToBoundaryCm = BaseDistanceCm +
                    FMath::Max(
                        0.0f,
                        ExpandedBeginDistanceCm - ProgressCm);
                if (PredictedAdvanceCm <
                    DistanceToBoundaryCm - 0.01f)
                {
                    continue;
                }

                TArray<int32> ClosureConflictIndices;
                GatherOverlappingLaneConflictClosure(
                    Record.Spawner,
                    LaneIndex,
                    ConflictIndex,
                    BodyClearanceCm,
                    ClosureConflictIndices);
                TArray<uint8> ClaimSides;
                ClaimSides.SetNumZeroed(
                    Record.Spawner->RuntimeCentralLocalConflicts.Num());
                AddClosureClaims(
                    Record.Spawner,
                    LaneIndex,
                    ClosureConflictIndices,
                    ClaimSides);
                if (RoutePathIndex != INDEX_NONE &&
                    RoutePathIndex + 1 < Record.PlannedLaneIndices.Num() &&
                    DoesClosureReachLaneEnd(
                        Record.Spawner,
                        LaneIndex,
                        ClosureConflictIndices,
                        BodyClearanceCm))
                {
                    AppendRouteEntryClaimChain(
                        Record,
                        RoutePathIndex + 1,
                        BodyClearanceCm,
                        ClaimSides);
                }
                AddClaimRequests(ClaimSides, DistanceToBoundaryCm);
            }
        };
        AddLaneRequests(
            Record.LaneIndex,
            Record.ProgressCm,
            0.0f,
            true,
            0);
        if (Record.bHasOutgoingTransition &&
            Record.NextLaneIndex != INDEX_NONE)
        {
            AddLaneRequests(
                Record.NextLaneIndex,
                0.0f,
                Record.DistanceToTransitionCm,
                false,
                Record.PlannedLaneIndices.Num() > 1 &&
                        Record.PlannedLaneIndices[1] == Record.NextLaneIndex
                    ? 1
                    : INDEX_NONE);
        }
        if (Candidate.Requests.IsEmpty())
        {
            continue;
        }
        for (const FCentralLocalConflictRequest& Request :
             Candidate.Requests)
        {
            Candidate.NearestBoundaryCm = FMath::Min(
                Candidate.NearestBoundaryCm,
                Request.DistanceToReservationBoundaryCm);
            if (Record.Spawner->RuntimeCentralLocalConflictWaitSeconds.
                    IsValidIndex(Record.EntityIndex) &&
                Record.Spawner->
                    RuntimeCentralLocalConflictWaitResourceIndices.
                    IsValidIndex(Record.EntityIndex))
            {
                const int32 StoredResourceIndex = Record.Spawner->
                    RuntimeCentralLocalConflictWaitResourceIndices[
                        Record.EntityIndex];
                const bool bSameResource =
                    StoredResourceIndex == Request.ConflictIndex;
                const bool bSameConflictCluster =
                    Record.Spawner->
                        RuntimeCentralLocalConflictClusterIndices.IsValidIndex(
                            StoredResourceIndex) &&
                    Record.Spawner->
                        RuntimeCentralLocalConflictClusterIndices.IsValidIndex(
                            Request.ConflictIndex) &&
                    Record.Spawner->RuntimeCentralLocalConflictClusterIndices[
                        StoredResourceIndex] ==
                        Record.Spawner->
                            RuntimeCentralLocalConflictClusterIndices[
                                Request.ConflictIndex];
                if (bSameResource || bSameConflictCluster)
                {
                    Candidate.ExistingWaitSeconds = FMath::Max(
                        Candidate.ExistingWaitSeconds,
                        Record.Spawner->
                            RuntimeCentralLocalConflictWaitSeconds[
                                Record.EntityIndex]);
                }
            }
        }
        LocalConflictCandidateEntities.Add(Record.Entity);
        LocalConflictCandidates.Add(MoveTemp(Candidate));
    }
    LocalConflictCandidates.Sort(
        [&Records](
            const FCentralLocalConflictCandidate& A,
            const FCentralLocalConflictCandidate& B)
        {
            const FCentralSpacingRecord& ARecord = Records[A.RecordIndex];
            const FCentralSpacingRecord& BRecord = Records[B.RecordIndex];
            if (ARecord.Spawner != BRecord.Spawner)
            {
                return ARecord.Spawner->GetUniqueID() <
                    BRecord.Spawner->GetUniqueID();
            }
            if (A.bHoldsAnotherResource != B.bHoldsAnotherResource)
            {
                return A.bHoldsAnotherResource;
            }
            if (!FMath::IsNearlyEqual(
                    A.ExistingWaitSeconds,
                    B.ExistingWaitSeconds))
            {
                // Wait age is a persistent lease until the requested resource
                // changes or is left.  Apply it to current occupants as well
                // as new entrants; placing stable entity order first starves a
                // higher-index occupant forever when a lower-index pedestrian
                // repeatedly reacquires the same recovery-lane closure.
                return A.ExistingWaitSeconds > B.ExistingWaitSeconds;
            }
            const float AArrivalSeconds = A.NearestBoundaryCm /
                FMath::Max(ARecord.TargetSpeedCmPerSecond, 1.0f);
            const float BArrivalSeconds = B.NearestBoundaryCm /
                FMath::Max(BRecord.TargetSpeedCmPerSecond, 1.0f);
            if (AArrivalSeconds != BArrivalSeconds)
            {
                return AArrivalSeconds < BArrivalSeconds;
            }
            return ARecord.EntityIndex < BRecord.EntityIndex;
        });

    TMap<AOpenMassCrowdSpawner*, TArray<uint8>> RequestedSidesBySpawner;
    for (const FCentralLocalConflictCandidate& Candidate :
         LocalConflictCandidates)
    {
        const FCentralSpacingRecord& Record = Records[Candidate.RecordIndex];
        TArray<uint8>& RequestedSides =
            RequestedSidesBySpawner.FindOrAdd(Record.Spawner);
        if (RequestedSides.Num() !=
            Record.Spawner->RuntimeCentralLocalConflicts.Num())
        {
            RequestedSides.SetNumZeroed(
                Record.Spawner->RuntimeCentralLocalConflicts.Num());
        }
        for (const FCentralLocalConflictRequest& Request :
             Candidate.Requests)
        {
            RequestedSides[Request.ConflictIndex] |= Request.SideMask;
        }
    }
    TArray<TArray<uint8>> GrantedClaimSidesByRecord;
    GrantedClaimSidesByRecord.SetNum(Records.Num());
    for (int32 RecordIndex = 0;
         RecordIndex < Records.Num();
         ++RecordIndex)
    {
        // Historic occupancy is evidence for this frame's requests, not an
        // irrevocable grant.  A loser stopped by the previous frame may no
        // longer be a movement candidate; carrying its old side forward would
        // immediately recreate the deadlock.  Moving occupants explicitly
        // request their current closure above.  Non-moving physical occupants
        // remain protected by the final pairwise 20 cm velocity clamp.
        GrantedClaimSidesByRecord[RecordIndex].SetNumZeroed(
            Records[RecordIndex].Spawner->
                RuntimeCentralLocalConflicts.Num());
    }
    for (FCentralLocalConflictCandidate& Candidate :
         LocalConflictCandidates)
    {
        FCentralSpacingRecord& Record = Records[Candidate.RecordIndex];
        TArray<uint8>& OwnGrantedSides =
            GrantedClaimSidesByRecord[Candidate.RecordIndex];
        if (OwnGrantedSides.Num() !=
            Record.Spawner->RuntimeCentralLocalConflicts.Num())
        {
            OwnGrantedSides.SetNumZeroed(
                Record.Spawner->RuntimeCentralLocalConflicts.Num());
        }
        const TArray<uint8>* RequestedSides =
            RequestedSidesBySpawner.Find(Record.Spawner);
        bool bGrantAll = true;
        int32 WaitResourceIndex = INDEX_NONE;
        for (const FCentralLocalConflictRequest& Request :
             Candidate.Requests)
        {
            const uint8 AllRequestedSides = RequestedSides &&
                    RequestedSides->IsValidIndex(Request.ConflictIndex)
                ? (*RequestedSides)[Request.ConflictIndex]
                : Request.SideMask;
            bool bAnyOtherInitialClaim = false;
            bool bOnlySameSideInitialClaims = Request.SideMask != 3;
            bool bIncompatibleOtherClaim = false;
            for (int32 OtherRecordIndex = 0;
                 OtherRecordIndex < Records.Num();
                 ++OtherRecordIndex)
            {
                if (OtherRecordIndex == Candidate.RecordIndex ||
                    Records[OtherRecordIndex].Spawner != Record.Spawner)
                {
                    continue;
                }
                const uint8 InitialOtherSide =
                    InitialClaimSidesByRecord[OtherRecordIndex].IsValidIndex(
                        Request.ConflictIndex)
                    ? InitialClaimSidesByRecord[OtherRecordIndex][
                        Request.ConflictIndex]
                    : 0;
                if (InitialOtherSide != 0)
                {
                    bAnyOtherInitialClaim = true;
                    bOnlySameSideInitialClaims =
                        bOnlySameSideInitialClaims &&
                        InitialOtherSide == Request.SideMask;
                    const bool bInitialClaimIsIncompatible =
                        Request.SideMask == 3 ||
                        InitialOtherSide == 3 ||
                        (InitialOtherSide & Request.SideMask) == 0;
                    const FCentralSpacingRecord& OtherRecord =
                        Records[OtherRecordIndex];
                    const bool bCandidateDrainsBlockedEntrantDestination =
                        OtherRecord.bDestinationStartOccupied &&
                        OtherRecord.NextLaneIndex == Record.LaneIndex;
                    if (bInitialClaimIsIncompatible &&
                        !LocalConflictCandidateEntities.Contains(
                            OtherRecord.Entity) &&
                        !bCandidateDrainsBlockedEntrantDestination)
                    {
                        // A variable-tick or route-boundary occupant may not
                        // submit a movement request this frame, but it is still
                        // physically inside the certified conflict interval.
                        // Do not let an entrant move through that stationary
                        // body. The one exception is the occupant already on a
                        // destination lane: the entry-headway guard has stopped
                        // its follower behind the transition, so that occupant
                        // must be allowed to drain away from the follower.
                        // Moving occupants continue through the ordered grant
                        // pass below so one owner can drain the resource.
                        bIncompatibleOtherClaim = true;
                        break;
                    }
                }
                const uint8 GrantedOtherSide =
                    GrantedClaimSidesByRecord[OtherRecordIndex].IsValidIndex(
                        Request.ConflictIndex)
                    ? GrantedClaimSidesByRecord[OtherRecordIndex][
                        Request.ConflictIndex]
                    : 0;
                if (GrantedOtherSide != 0 &&
                    (Request.SideMask == 3 ||
                     GrantedOtherSide == 3 ||
                     (GrantedOtherSide & Request.SideMask) == 0))
                {
                    bIncompatibleOtherClaim = true;
                    break;
                }
            }
            const bool bDrainForOppositeWaiter =
                !Candidate.bHoldsAnotherResource &&
                bAnyOtherInitialClaim &&
                bOnlySameSideInitialClaims &&
                (AllRequestedSides & ~Request.SideMask) != 0;
            if (bEnableCentralLocalConflictReservations &&
                (bIncompatibleOtherClaim ||
                 bDrainForOppositeWaiter))
            {
                bGrantAll = false;
                WaitResourceIndex = WaitResourceIndex == INDEX_NONE
                    ? Request.ConflictIndex
                    : FMath::Min(
                        WaitResourceIndex,
                        Request.ConflictIndex);
            }
        }

        if (bGrantAll)
        {
            for (const FCentralLocalConflictRequest& Request :
                 Candidate.Requests)
            {
                OwnGrantedSides[Request.ConflictIndex] |= Request.SideMask;
            }
            // Keep the wait age while this entity still requests the same
            // resource.  It is the lease that lets one selected entrant keep
            // priority long enough to cross the boundary.  Clearing it on
            // the first granted frame moves that entrant behind the loser on
            // the next frame, so two approaches can alternate grants forever
            // without either one draining.  The cleanup pass below resets the
            // age only after the entity has actually left every candidate
            // resource; the deny path resets it when the contested resource
            // itself changes.
            continue;
        }

        const float MaximumAdvanceBeforeAnyResourceCm = FMath::Max(
            0.0f,
            Candidate.NearestBoundaryCm - 0.01f);
        Record.TargetSpeedCmPerSecond = FMath::Min(
            Record.TargetSpeedCmPerSecond,
            FMath::FloorToFloat(
                MaximumAdvanceBeforeAnyResourceCm /
                FMath::Max(Record.TickDeltaSeconds, 0.001f)));
        Record.Spawner->CentralLocalConflictHoldCount =
            Record.Spawner->CentralLocalConflictHoldCount >= MAX_int32
            ? MAX_int32
            : Record.Spawner->CentralLocalConflictHoldCount + 1;

        float WaitSeconds = 0.0f;
        if (Record.Spawner->RuntimeCentralLocalConflictWaitSeconds.
                IsValidIndex(Record.EntityIndex) &&
            Record.Spawner->RuntimeCentralLocalConflictWaitResourceIndices.
                IsValidIndex(Record.EntityIndex))
        {
            float& StoredWaitSeconds =
                Record.Spawner->RuntimeCentralLocalConflictWaitSeconds[
                    Record.EntityIndex];
            int32& StoredResourceIndex =
                Record.Spawner->
                    RuntimeCentralLocalConflictWaitResourceIndices[
                        Record.EntityIndex];
            const bool bSameConflictCluster =
                Record.Spawner->RuntimeCentralLocalConflictClusterIndices.
                    IsValidIndex(StoredResourceIndex) &&
                Record.Spawner->RuntimeCentralLocalConflictClusterIndices.
                    IsValidIndex(WaitResourceIndex) &&
                Record.Spawner->RuntimeCentralLocalConflictClusterIndices[
                    StoredResourceIndex] ==
                    Record.Spawner->RuntimeCentralLocalConflictClusterIndices[
                        WaitResourceIndex];
            if (StoredResourceIndex != WaitResourceIndex &&
                !bSameConflictCluster)
            {
                StoredWaitSeconds = 0.0f;
            }
            StoredResourceIndex = WaitResourceIndex;
            StoredWaitSeconds = FMath::Min(
                3600.0f,
                StoredWaitSeconds +
                    FMath::Max(Record.TickDeltaSeconds, 0.0f));
            WaitSeconds = StoredWaitSeconds;
            Record.Spawner->CentralMaximumConflictWaitSeconds = FMath::Max(
                Record.Spawner->CentralMaximumConflictWaitSeconds,
                WaitSeconds);
        }
        const float PreviousWaitSeconds = FMath::Max(
            0.0f,
            WaitSeconds - FMath::Max(Record.TickDeltaSeconds, 0.0f));
        const int32 PreviousReplanBucket = FMath::FloorToInt(
            PreviousWaitSeconds / CentralLocalConflictReplanSeconds);
        const int32 CurrentReplanBucket = FMath::FloorToInt(
            WaitSeconds / CentralLocalConflictReplanSeconds);
        const int32 PreviousDiagnosticBucket = FMath::FloorToInt(
            PreviousWaitSeconds / 5.0f);
        const int32 CurrentDiagnosticBucket = FMath::FloorToInt(
            WaitSeconds / 5.0f);
        if (WaitResourceIndex != INDEX_NONE &&
            CurrentDiagnosticBucket > PreviousDiagnosticBucket &&
            CurrentDiagnosticBucket >= 1)
        {
            FString Claimants;
            for (int32 OtherRecordIndex = 0;
                 OtherRecordIndex < Records.Num();
                 ++OtherRecordIndex)
            {
                if (Records[OtherRecordIndex].Spawner != Record.Spawner ||
                    !InitialClaimSidesByRecord[OtherRecordIndex].IsValidIndex(
                        WaitResourceIndex) ||
                    InitialClaimSidesByRecord[OtherRecordIndex][
                        WaitResourceIndex] == 0)
                {
                    continue;
                }
                const FCentralSpacingRecord& Claimant =
                    Records[OtherRecordIndex];
                if (!Claimants.IsEmpty())
                {
                    Claimants += TEXT("|");
                }
                Claimants += FString::Printf(
                    TEXT("e%d:l%d:p%.1f:side%d:active%d:advance%d:speed%.1f:next%d:entry_blocked%d"),
                    Claimant.EntityIndex,
                    Claimant.LaneIndex,
                    Claimant.ProgressCm,
                    InitialClaimSidesByRecord[OtherRecordIndex][
                        WaitResourceIndex],
                    Claimant.bActiveMove ? 1 : 0,
                    Claimant.bWillAdvance ? 1 : 0,
                    Claimant.TargetSpeedCmPerSecond,
                    Claimant.NextLaneIndex,
                    Claimant.bDestinationStartOccupied ? 1 : 0);
            }
            UE_LOG(
                LogTemp,
                Warning,
                TEXT("OPEN_MASS_CROWD_CENTRAL_CONFLICT_WAIT_DIAGNOSTIC waiter=e%d resource=%d wait_s=%.2f lane=%d progress=%.1f next=%d claimants=%s"),
                Record.EntityIndex,
                WaitResourceIndex,
                WaitSeconds,
                Record.LaneIndex,
                Record.ProgressCm,
                Record.NextLaneIndex,
                Claimants.IsEmpty() ? TEXT("none") : *Claimants);
        }
        // Every waiter, including a current node/local-resource owner, must be
        // allowed to replace the contested next hop.  Keeping an owner on the
        // same request forever turns a finite reservation into an unbounded
        // hold-and-wait cycle.  The current physical claim remains active for
        // this tick, while QueueCentralConflictReplan excludes only the actual
        // next lane and lets the following tick request a different certified
        // route.  The post-arbitration 20 cm clamp remains the hard safety net.
        if (bEnableCentralConflictWaitReplans &&
            CurrentReplanBucket > PreviousReplanBucket)
        {
            TSet<int32> ForbiddenLaneIndices;
            if (Record.bHasOutgoingTransition &&
                Record.NextLaneIndex != INDEX_NONE &&
                Record.NextLaneIndex != Record.LaneIndex)
            {
                // Route planning excludes only the actual next topological
                // lane.  The 55 cm local-resource closure is a collision
                // reservation, not a road-closure set: expanding it into every
                // geometrically adjacent lane can remove every real exit from
                // a junction and make the waiter retry forever.  The existing
                // local/node reservations continue to enforce the 20 cm hard
                // clearance while A* chooses a different certified first hop.
                ForbiddenLaneIndices.Add(Record.NextLaneIndex);
            }
            Record.Spawner->QueueCentralConflictReplan(
                Record.EntityIndex,
                ForbiddenLaneIndices);
        }
    }
    for (const FCentralSpacingRecord& Record : Records)
    {
        if (!LocalConflictCandidateEntities.Contains(Record.Entity))
        {
            if (Record.Spawner->RuntimeCentralLocalConflictWaitSeconds.
                    IsValidIndex(Record.EntityIndex))
            {
                Record.Spawner->RuntimeCentralLocalConflictWaitSeconds[
                    Record.EntityIndex] = 0.0f;
            }
            if (Record.Spawner->
                    RuntimeCentralLocalConflictWaitResourceIndices.
                    IsValidIndex(Record.EntityIndex))
            {
                Record.Spawner->
                    RuntimeCentralLocalConflictWaitResourceIndices[
                        Record.EntityIndex] = INDEX_NONE;
            }
        }
    }
    }

    for (const FCentralSpacingRecord& Record : Records)
    {
        if (Record.Spawner->RuntimeCentralCorridorWaitSeconds.IsValidIndex(
                Record.EntityIndex))
        {
            const float CorridorWaitSeconds =
                Record.Spawner->RuntimeCentralCorridorWaitSeconds[
                    Record.EntityIndex];
            Record.Spawner->CentralMaximumConflictWaitSeconds = FMath::Max(
                Record.Spawner->CentralMaximumConflictWaitSeconds,
                CorridorWaitSeconds);
            const float PreviousCorridorWaitSeconds = FMath::Max(
                0.0f,
                CorridorWaitSeconds -
                    FMath::Max(Record.TickDeltaSeconds, 0.0f));
            const int32 PreviousCorridorReplanBucket = FMath::FloorToInt(
                PreviousCorridorWaitSeconds /
                    CentralLocalConflictReplanSeconds);
            const int32 CurrentCorridorReplanBucket = FMath::FloorToInt(
                CorridorWaitSeconds / CentralLocalConflictReplanSeconds);
            if (bEnableCentralConflictWaitReplans &&
                CurrentCorridorReplanBucket >
                    PreviousCorridorReplanBucket &&
                Record.Spawner->RuntimeCentralCorridorWaitLaneIndices.
                    IsValidIndex(Record.EntityIndex))
            {
                const int32 WaitLaneIndex =
                    Record.Spawner->RuntimeCentralCorridorWaitLaneIndices[
                        Record.EntityIndex];
                TSet<int32> ForbiddenLaneIndices;
                if (WaitLaneIndex != INDEX_NONE)
                {
                    // This is a route-planning exclusion, not a collision
                    // closure.  Forbidding every geometrically adjacent lane
                    // can remove all real exits from a small junction and
                    // turn a bounded wait into a permanent retry loop.
                    ForbiddenLaneIndices.Add(WaitLaneIndex);
                }
                Record.Spawner->QueueCentralConflictReplan(
                    Record.EntityIndex,
                    ForbiddenLaneIndices);
            }
        }
    }

    // Node, transition, and local-interval reservations above may
    // lower a leader's
    // speed after the first headway pass.  Converge headway once more as the
    // final speed cap, without crediting any future leader movement: steering,
    // variable ticking, or another reservation can prevent that movement from
    // being realised.  This conservative pass therefore guarantees the
    // current-frame body clearance even if the leader does not advance at all.
    for (int32 RecordIndex = 1; RecordIndex < Records.Num(); ++RecordIndex)
    {
        FCentralSpacingRecord& Follower = Records[RecordIndex];
        if (!Follower.bActiveMove)
        {
            continue;
        }

        int32 LeaderRecordIndex = INDEX_NONE;
        float NearestLeaderDistanceCm = TNumericLimits<float>::Max();
        for (int32 CandidateIndex = 0;
             CandidateIndex < RecordIndex;
             ++CandidateIndex)
        {
            const FCentralSpacingRecord& Candidate = Records[CandidateIndex];
            if (!AreSameDirectionPhysicalLanes(Follower, Candidate))
            {
                continue;
            }
            const float RealisedDistanceCm = FVector::Distance(
                Candidate.Position,
                Follower.Position);
            const float CandidateDistanceCm =
                Candidate.LaneIndex == Follower.LaneIndex &&
                Candidate.PresentationBandIndex ==
                    Follower.PresentationBandIndex
                ? FMath::Min(
                    RealisedDistanceCm,
                    FMath::Abs(
                        Candidate.ProgressCm - Follower.ProgressCm))
                : RealisedDistanceCm;
            if (CandidateDistanceCm >= 0.0f &&
                CandidateDistanceCm < NearestLeaderDistanceCm)
            {
                LeaderRecordIndex = CandidateIndex;
                NearestLeaderDistanceCm = CandidateDistanceCm;
            }
        }
        if (LeaderRecordIndex == INDEX_NONE)
        {
            continue;
        }

        const FCentralSpacingRecord& Leader = Records[LeaderRecordIndex];
        const float RequiredCenterDistanceCm =
            Leader.RadiusCm + Follower.RadiusCm +
            CentralHeadwaySafetyGapCm;
        if (NearestLeaderDistanceCm <= RequiredCenterDistanceCm)
        {
            Follower.TargetSpeedCmPerSecond = 0.0f;
            continue;
        }
        if (!Follower.bWillAdvance)
        {
            continue;
        }

        const float MaximumFollowerAdvanceCm = FMath::Max(
            0.0f,
            NearestLeaderDistanceCm - RequiredCenterDistanceCm -
                CentralLaneDistanceQuantizationGuardCm);
        const float MaximumFollowerSpeedCmPerSecond = FMath::FloorToFloat(
            MaximumFollowerAdvanceCm /
            FMath::Max(Follower.TickDeltaSeconds, 0.001f));
        Follower.TargetSpeedCmPerSecond = FMath::Min(
            Follower.TargetSpeedCmPerSecond,
            MaximumFollowerSpeedCmPerSecond);
    }

    // Disabled after Gate100 showed that pre-emptively holding every guarded
    // prediction amplified queues.  The post-movement certified rollback below
    // is both stricter and less invasive because it acts only on a realised
    // unsafe transform.
    if constexpr (false)
    {
    // The interval table proves every certified cross-lane pair below the hard
    // gate, but a route can be replaced at a node after those interval claims
    // were assembled.  Perform one final geometry-level prediction against the
    // actual route selected for this frame.  Holds only reduce speed, so the
    // fixed-point pass converges monotonically and cannot push or teleport an
    // entity.  Far pairs retain one ZoneGraph distance quantum of guard; a pair
    // already inside that guard may separate, but can never get closer.
    const auto PredictCertifiedPosition = [](
        const FCentralSpacingRecord& Record) -> FVector
    {
        if (!Record.bWillAdvance ||
            Record.TargetSpeedCmPerSecond <= 0.0f ||
            !FMath::IsFinite(Record.TickDeltaSeconds) ||
            Record.TickDeltaSeconds <= 0.0f)
        {
            return Record.Position;
        }

        UZoneGraphSubsystem* ZoneGraphSubsystem =
            UWorld::GetSubsystem<UZoneGraphSubsystem>(
                Record.Spawner->GetWorld());
        if (!ZoneGraphSubsystem ||
            !Record.Spawner->RuntimeLaneHandles.IsValidIndex(
                Record.LaneIndex))
        {
            return Record.Position;
        }

        const float AdvanceCm = Record.TargetSpeedCmPerSecond *
            Record.TickDeltaSeconds;
        int32 PredictedLaneIndex = Record.LaneIndex;
        float PredictedDistanceAlongLaneCm =
            Record.ProgressCm + AdvanceCm;
        if (Record.bHasOutgoingTransition &&
            Record.NextLaneIndex != INDEX_NONE &&
            Record.Spawner->RuntimeLaneHandles.IsValidIndex(
                Record.NextLaneIndex) &&
            AdvanceCm > Record.DistanceToNodeCm)
        {
            PredictedLaneIndex = Record.NextLaneIndex;
            PredictedDistanceAlongLaneCm = FMath::Max(
                0.0f,
                AdvanceCm - Record.DistanceToNodeCm);
        }

        float PredictedLaneLengthCm = Record.LaneLengthCm;
        if (PredictedLaneIndex != Record.LaneIndex &&
            Record.Spawner->RuntimeCentralLaneLengthsCm.IsValidIndex(
                PredictedLaneIndex))
        {
            PredictedLaneLengthCm =
                Record.Spawner->RuntimeCentralLaneLengthsCm[
                    PredictedLaneIndex];
        }
        PredictedDistanceAlongLaneCm = FMath::Clamp(
            PredictedDistanceAlongLaneCm,
            0.0f,
            FMath::Max(0.0f, PredictedLaneLengthCm));

        FZoneGraphLaneLocation PredictedLocation;
        if (!ZoneGraphSubsystem->CalculateLocationAlongLane(
                Record.Spawner->RuntimeLaneHandles[PredictedLaneIndex],
                PredictedDistanceAlongLaneCm,
                PredictedLocation))
        {
            return Record.Position;
        }
        return PredictedLocation.Position;
    };

    TArray<FVector> PredictedPositions;
    PredictedPositions.SetNum(Records.Num());
    TArray<uint8> HoldRecords;
    HoldRecords.SetNumZeroed(Records.Num());
    for (int32 ConvergencePass = 0;
         ConvergencePass < Records.Num();
         ++ConvergencePass)
    {
        for (int32 RecordIndex = 0;
             RecordIndex < Records.Num();
             ++RecordIndex)
        {
            PredictedPositions[RecordIndex] =
                PredictCertifiedPosition(Records[RecordIndex]);
            HoldRecords[RecordIndex] = 0;
        }

        bool bFoundUnsafePrediction = false;
        for (int32 FirstIndex = 0;
             FirstIndex < Records.Num() - 1;
             ++FirstIndex)
        {
            for (int32 SecondIndex = FirstIndex + 1;
                 SecondIndex < Records.Num();
                 ++SecondIndex)
            {
                const FCentralSpacingRecord& First = Records[FirstIndex];
                const FCentralSpacingRecord& Second = Records[SecondIndex];
                if (First.Spawner != Second.Spawner)
                {
                    continue;
                }

                const float CurrentDistanceCm = FVector::Distance(
                    First.Position,
                    Second.Position);
                const float GuardedClearanceCm =
                    CentralHardConflictClearanceCm +
                    CentralLaneDistanceQuantizationGuardCm;
                // Once within the quantization guard, monotonic separation is
                // enough: requiring an immediate 10 cm jump would stop both
                // bodies and make recovery from a pre-existing bad frame
                // impossible.
                const float RequiredPredictedDistanceCm =
                    CurrentDistanceCm >= GuardedClearanceCm
                    ? GuardedClearanceCm
                    : CurrentDistanceCm;
                const float PredictedDistanceCm = FVector::Distance(
                    PredictedPositions[FirstIndex],
                    PredictedPositions[SecondIndex]);
                if (PredictedDistanceCm + 0.001f >=
                    RequiredPredictedDistanceCm)
                {
                    continue;
                }

                const bool bFirstCanMoveAlone = FVector::Distance(
                        PredictedPositions[FirstIndex],
                        Second.Position) + 0.001f >=
                    RequiredPredictedDistanceCm;
                const bool bSecondCanMoveAlone = FVector::Distance(
                        First.Position,
                        PredictedPositions[SecondIndex]) + 0.001f >=
                    RequiredPredictedDistanceCm;
                if (bFirstCanMoveAlone && !bSecondCanMoveAlone)
                {
                    HoldRecords[SecondIndex] = 1;
                }
                else if (!bFirstCanMoveAlone && bSecondCanMoveAlone)
                {
                    HoldRecords[FirstIndex] = 1;
                }
                else if (bFirstCanMoveAlone && bSecondCanMoveAlone)
                {
                    const int32 LoserIndex =
                        First.EntityIndex < Second.EntityIndex
                        ? SecondIndex
                        : FirstIndex;
                    HoldRecords[LoserIndex] = 1;
                }
                else
                {
                    HoldRecords[FirstIndex] = 1;
                    HoldRecords[SecondIndex] = 1;
                }
                bFoundUnsafePrediction = true;
            }
        }

        bool bAppliedNewHold = false;
        if (bFoundUnsafePrediction)
        {
            for (int32 RecordIndex = 0;
                 RecordIndex < Records.Num();
                 ++RecordIndex)
            {
                if (HoldRecords[RecordIndex] != 0 &&
                    Records[RecordIndex].TargetSpeedCmPerSecond > 0.0f)
                {
                    Records[RecordIndex].TargetSpeedCmPerSecond = 0.0f;
                    bAppliedNewHold = true;
                }
            }
        }
        if (!bAppliedNewHold)
        {
            break;
        }
    }
    }

    for (const FCentralSpacingRecord& Record : Records)
    {
        if (!EntityManager.IsEntityValid(Record.Entity))
        {
            continue;
        }
        FMassMoveTargetFragment& MoveTarget =
            EntityManager.GetFragmentDataChecked<FMassMoveTargetFragment>(
                Record.Entity);
        const FMassInt16Real EncodedSpeed(
            FMath::Max(0.0f, Record.TargetSpeedCmPerSecond));
        if (MoveTarget.DesiredSpeed != EncodedSpeed)
        {
            MoveTarget.DesiredSpeed = EncodedSpeed;
            MoveTarget.MarkNetDirty();
        }
    }
}

UOpenMassCrowdPostAvoidanceReservationProcessor::
    UOpenMassCrowdPostAvoidanceReservationProcessor()
    : EntityQuery(*this)
{
    ExecutionFlags = static_cast<int32>(EProcessorExecutionFlags::AllNetModes);
    ProcessingPhase = EMassProcessingPhase::PrePhysics;
    bAutoRegisterWithProcessingPhases = true;
    bRequiresGameThreadExecution = true;

    // UE 5.7 ApplyForces writes the final DesiredVelocity after avoidance;
    // ApplyMovement then copies it into velocity and integrates the transform.
    // Enforce the reservation in that exact gap so a stale pre-reservation
    // velocity cannot advance an entity whose DesiredSpeed was reduced to zero.
    ExecutionOrder.ExecuteInGroup = UE::Mass::ProcessorGroupNames::Movement;
    ExecutionOrder.ExecuteAfter.Add(
        UE::Mass::ProcessorGroupNames::ApplyForces);
    ExecutionOrder.ExecuteBefore.Add(
        UMassApplyMovementProcessor::StaticClass()->GetFName());
}

void UOpenMassCrowdPostAvoidanceReservationProcessor::ConfigureQueries(
    const TSharedRef<FMassEntityManager>& EntityManager)
{
    EntityQuery.AddTagRequirement<FOpenMassCrowdTag>(
        EMassFragmentPresence::All);
    EntityQuery.AddRequirement<FOpenMassCrowdCentralVisualOwnerFragment>(
        EMassFragmentAccess::ReadOnly);
    EntityQuery.AddRequirement<FMassMoveTargetFragment>(
        EMassFragmentAccess::ReadOnly);
    EntityQuery.AddRequirement<FMassDesiredMovementFragment>(
        EMassFragmentAccess::ReadWrite);
}

void UOpenMassCrowdPostAvoidanceReservationProcessor::Execute(
    FMassEntityManager& EntityManager,
    FMassExecutionContext& Context)
{
    EntityQuery.ForEachEntityChunk(
        Context,
        [](FMassExecutionContext& Context)
        {
            const TConstArrayView<FOpenMassCrowdCentralVisualOwnerFragment>
                OwnerList = Context.GetFragmentView<
                    FOpenMassCrowdCentralVisualOwnerFragment>();
            const TConstArrayView<FMassMoveTargetFragment> MoveTargetList =
                Context.GetFragmentView<FMassMoveTargetFragment>();
            const TArrayView<FMassDesiredMovementFragment> DesiredMovementList =
                Context.GetMutableFragmentView<FMassDesiredMovementFragment>();

            for (FMassExecutionContext::FEntityIterator EntityIt =
                     Context.CreateEntityIterator();
                 EntityIt;
                 ++EntityIt)
            {
                const FOpenMassCrowdCentralVisualOwnerFragment& Owner =
                    OwnerList[EntityIt];
                AOpenMassCrowdSpawner* Spawner = Owner.Spawner.Get();
                const FMassEntityHandle Entity = Context.GetEntity(EntityIt);
                if (!IsValid(Spawner) ||
                    Spawner->NetworkMode !=
                        EOpenMassCrowdNetworkMode::CentralCertifiedCache ||
                    !Spawner->SpawnedEntities.IsValidIndex(Owner.EntityIndex) ||
                    Spawner->SpawnedEntities[Owner.EntityIndex] != Entity)
                {
                    continue;
                }

                FMassDesiredMovementFragment& DesiredMovement =
                    DesiredMovementList[EntityIt];
                const float SpeedCapCmPerSecond = FMath::Max(
                    0.0f,
                    MoveTargetList[EntityIt].DesiredSpeed.Get());
                const float DesiredSpeedSquared =
                    DesiredMovement.DesiredVelocity.SizeSquared();
                if (!FMath::IsFinite(DesiredSpeedSquared) ||
                    SpeedCapCmPerSecond <= 0.0f)
                {
                    DesiredMovement.DesiredVelocity = FVector::ZeroVector;
                }
                else if (DesiredSpeedSquared >
                         FMath::Square(SpeedCapCmPerSecond))
                {
                    DesiredMovement.DesiredVelocity =
                        DesiredMovement.DesiredVelocity.GetClampedToMaxSize(
                            SpeedCapCmPerSecond);
                }
            }
        });
}

UOpenMassCrowdCertifiedTransformProcessor::
    UOpenMassCrowdCertifiedTransformProcessor()
    : EntityQuery(*this)
{
    ExecutionFlags = static_cast<int32>(
        EProcessorExecutionFlags::Client |
        EProcessorExecutionFlags::Standalone);
    ProcessingPhase = EMassProcessingPhase::PrePhysics;
    bAutoRegisterWithProcessingPhases = true;
    bRequiresGameThreadExecution = true;

    // Movement includes avoidance's final transform integration. Clamp after
    // it, but before either actor/LOD representation selection or the ISM
    // transform batch reads FTransformFragment.
    ExecutionOrder.ExecuteInGroup =
        UE::Mass::ProcessorGroupNames::Representation;
    ExecutionOrder.ExecuteAfter.Add(
        UE::Mass::ProcessorGroupNames::Movement);
    ExecutionOrder.ExecuteBefore.Add(
        UMassCrowdVisualizationProcessor::StaticClass()->GetFName());
    ExecutionOrder.ExecuteBefore.Add(
        UMassUpdateISMProcessor::StaticClass()->GetFName());
}

void UOpenMassCrowdCertifiedTransformProcessor::ConfigureQueries(
    const TSharedRef<FMassEntityManager>& EntityManager)
{
    EntityQuery.AddTagRequirement<FOpenMassCrowdTag>(
        EMassFragmentPresence::All);
    EntityQuery.AddRequirement<FOpenMassCrowdCentralVisualOwnerFragment>(
        EMassFragmentAccess::ReadOnly);
    EntityQuery.AddRequirement<FTransformFragment>(
        EMassFragmentAccess::ReadWrite);
    EntityQuery.AddRequirement<FMassZoneGraphLaneLocationFragment>(
        EMassFragmentAccess::ReadWrite);
    EntityQuery.AddRequirement<FMassVelocityFragment>(
        EMassFragmentAccess::ReadWrite);
    EntityQuery.AddRequirement<FMassZoneGraphShortPathFragment>(
        EMassFragmentAccess::ReadWrite);
    EntityQuery.AddRequirement<FMassMoveTargetFragment>(
        EMassFragmentAccess::ReadWrite);
}

void UOpenMassCrowdCertifiedTransformProcessor::Execute(
    FMassEntityManager& EntityManager,
    FMassExecutionContext& Context)
{
    // Actor Tick precedes the PrePhysics Mass pipeline. A liveness step made
    // there is overwritten by PathFollow later in the same frame, which was
    // the reason several apparently healthy pedestrians accumulated stationary
    // time forever. Gather the active Central spawners first, then perform the
    // exact-lane constraint and any certified recovery only here, after the
    // Movement group and before representation consumes the transform.
    TSet<AOpenMassCrowdSpawner*> ActiveCentralSpawners;
    EntityQuery.ForEachEntityChunk(
        Context,
        [&ActiveCentralSpawners](FMassExecutionContext& Context)
        {
            const TConstArrayView<FOpenMassCrowdCentralVisualOwnerFragment>
                OwnerList = Context.GetFragmentView<
                    FOpenMassCrowdCentralVisualOwnerFragment>();
            for (FMassExecutionContext::FEntityIterator EntityIt =
                     Context.CreateEntityIterator();
                 EntityIt;
                 ++EntityIt)
            {
                if (AOpenMassCrowdSpawner* Spawner =
                        OwnerList[EntityIt].Spawner.Get();
                    IsValid(Spawner))
                {
                    ActiveCentralSpawners.Add(Spawner);
                }
            }
        });
    for (AOpenMassCrowdSpawner* Spawner : ActiveCentralSpawners)
    {
        Spawner->ConstrainCentralTransformsToCertifiedLanes();
    }

    struct FCentralCertifiedFrameRecord
    {
        FMassEntityHandle Entity;
        AOpenMassCrowdSpawner* Spawner = nullptr;
        int32 EntityIndex = INDEX_NONE;
        AOpenMassCrowdSpawner::FLastValidGroundState PreviousState;
        AOpenMassCrowdSpawner::FLastValidGroundState CandidateState;
        FMassZoneGraphShortPathFragment PreviousShortPath;
        FMassMoveTargetFragment PreviousMoveTarget;
        bool bPreviousNavigationValid = false;
        AOpenMassCrowdSpawner::FLastValidGroundState YieldAnchorState;
        FMassZoneGraphShortPathFragment YieldAnchorShortPath;
        FMassMoveTargetFragment YieldAnchorMoveTarget;
        bool bYieldAnchorNavigationValid = false;
        float StationarySeconds = 0.0f;
    };
    TArray<FCentralCertifiedFrameRecord> Records;
    EntityQuery.ForEachEntityChunk(
        Context,
        [&Records](FMassExecutionContext& Context)
        {
            const TConstArrayView<FOpenMassCrowdCentralVisualOwnerFragment>
                OwnerList = Context.GetFragmentView<
                    FOpenMassCrowdCentralVisualOwnerFragment>();
            const TArrayView<FTransformFragment> TransformList =
                Context.GetMutableFragmentView<FTransformFragment>();
            const TArrayView<FMassZoneGraphLaneLocationFragment> LaneList =
                Context.GetMutableFragmentView<
                    FMassZoneGraphLaneLocationFragment>();
            const TArrayView<FMassVelocityFragment> VelocityList =
                Context.GetMutableFragmentView<FMassVelocityFragment>();
            const TArrayView<FMassZoneGraphShortPathFragment> ShortPathList =
                Context.GetMutableFragmentView<
                    FMassZoneGraphShortPathFragment>();
            const TArrayView<FMassMoveTargetFragment> MoveTargetList =
                Context.GetMutableFragmentView<FMassMoveTargetFragment>();
            for (FMassExecutionContext::FEntityIterator EntityIt =
                     Context.CreateEntityIterator();
                 EntityIt;
                 ++EntityIt)
            {
                const FOpenMassCrowdCentralVisualOwnerFragment& Owner =
                    OwnerList[EntityIt];
                AOpenMassCrowdSpawner* Spawner = Owner.Spawner.Get();
                if (!IsValid(Spawner) || Owner.EntityIndex == INDEX_NONE)
                {
                    // Local30 leaves the fragment empty and is byte-for-byte
                    // outside this Central-only transform path.
                    continue;
                }
                if (Spawner->RuntimeCentralPreviousFrameStates.Num() <
                    Spawner->SpawnedEntities.Num())
                {
                    Spawner->RuntimeCentralPreviousFrameStates.SetNum(
                        Spawner->SpawnedEntities.Num());
                }
                if (Spawner->RuntimeCentralPreviousFrameShortPaths.Num() <
                    Spawner->SpawnedEntities.Num())
                {
                    Spawner->RuntimeCentralPreviousFrameShortPaths.SetNum(
                        Spawner->SpawnedEntities.Num());
                    Spawner->RuntimeCentralPreviousFrameMoveTargets.SetNum(
                        Spawner->SpawnedEntities.Num());
                    Spawner->RuntimeCentralPreviousFrameNavigationValid.
                        SetNumZeroed(Spawner->SpawnedEntities.Num());
                }
                if (Spawner->RuntimeCentralYieldAnchorStates.Num() <
                    Spawner->SpawnedEntities.Num())
                {
                    Spawner->RuntimeCentralYieldAnchorStates.SetNum(
                        Spawner->SpawnedEntities.Num());
                    Spawner->RuntimeCentralYieldAnchorShortPaths.SetNum(
                        Spawner->SpawnedEntities.Num());
                    Spawner->RuntimeCentralYieldAnchorMoveTargets.SetNum(
                        Spawner->SpawnedEntities.Num());
                    Spawner->RuntimeCentralYieldAnchorNavigationValid.
                        SetNumZeroed(Spawner->SpawnedEntities.Num());
                }

                FCentralCertifiedFrameRecord& Record =
                    Records.AddDefaulted_GetRef();
                Record.Entity = Context.GetEntity(EntityIt);
                Record.Spawner = Spawner;
                Record.EntityIndex = Owner.EntityIndex;
                if (Spawner->CentralTelemetryStationarySeconds.IsValidIndex(
                        Owner.EntityIndex))
                {
                    Record.StationarySeconds =
                        Spawner->CentralTelemetryStationarySeconds[
                            Owner.EntityIndex];
                }
                if (Spawner->RuntimeCentralPreviousFrameStates.IsValidIndex(
                        Owner.EntityIndex) &&
                    Spawner->RuntimeCentralPreviousFrameStates[
                        Owner.EntityIndex].bValid)
                {
                    Record.PreviousState =
                        Spawner->RuntimeCentralPreviousFrameStates[
                            Owner.EntityIndex];
                }
                else
                {
                    Record.PreviousState.Transform =
                        TransformList[EntityIt].GetTransform();
                    Record.PreviousState.LaneHandle =
                        LaneList[EntityIt].LaneHandle;
                    Record.PreviousState.DistanceAlongLane =
                        LaneList[EntityIt].DistanceAlongLane;
                    Record.PreviousState.LaneLength =
                        LaneList[EntityIt].LaneLength;
                    Record.PreviousState.bValid = true;
                }
                if (Spawner->RuntimeCentralPreviousFrameNavigationValid.
                        IsValidIndex(Owner.EntityIndex) &&
                    Spawner->RuntimeCentralPreviousFrameNavigationValid[
                        Owner.EntityIndex] != 0)
                {
                    Record.PreviousShortPath =
                        Spawner->RuntimeCentralPreviousFrameShortPaths[
                            Owner.EntityIndex];
                    Record.PreviousMoveTarget =
                        Spawner->RuntimeCentralPreviousFrameMoveTargets[
                            Owner.EntityIndex];
                    Record.bPreviousNavigationValid = true;
                }
                else
                {
                    Record.PreviousShortPath = ShortPathList[EntityIt];
                    Record.PreviousMoveTarget = MoveTargetList[EntityIt];
                }
                if (Spawner->RuntimeCentralYieldAnchorStates.IsValidIndex(
                        Owner.EntityIndex) &&
                    Spawner->RuntimeCentralYieldAnchorStates[
                        Owner.EntityIndex].bValid &&
                    Spawner->RuntimeCentralYieldAnchorNavigationValid.
                        IsValidIndex(Owner.EntityIndex) &&
                    Spawner->RuntimeCentralYieldAnchorNavigationValid[
                        Owner.EntityIndex] != 0)
                {
                    Record.YieldAnchorState =
                        Spawner->RuntimeCentralYieldAnchorStates[
                            Owner.EntityIndex];
                    Record.YieldAnchorShortPath =
                        Spawner->RuntimeCentralYieldAnchorShortPaths[
                            Owner.EntityIndex];
                    Record.YieldAnchorMoveTarget =
                        Spawner->RuntimeCentralYieldAnchorMoveTargets[
                            Owner.EntityIndex];
                    Record.bYieldAnchorNavigationValid = true;
                }
                Spawner->ConstrainCentralEntityTransform(
                    Owner.EntityIndex,
                    TransformList[EntityIt],
                    LaneList[EntityIt],
                    &VelocityList[EntityIt]);

                Record.CandidateState.Transform =
                    TransformList[EntityIt].GetTransform();
                Record.CandidateState.LaneHandle =
                    LaneList[EntityIt].LaneHandle;
                Record.CandidateState.DistanceAlongLane =
                    LaneList[EntityIt].DistanceAlongLane;
                Record.CandidateState.LaneLength =
                    LaneList[EntityIt].LaneLength;
                Record.CandidateState.bValid = true;
            }
        });

    // This processor runs after Mass has integrated movement but before actor
    // and ISM representation reads the transform.  Resolve any realised hard
    // overlap by restoring one deterministic participant to its immediately
    // previous certified state.  Previous-frame states began clearance-safe at
    // admission, so rolling both members back is the fail-closed proof when a
    // one-sided rollback is insufficient.
    constexpr float CentralHardCollisionDistanceCm = 20.0f;
    const float CentralHardCollisionDistanceSquared = FMath::Square(
        CentralHardCollisionDistanceCm);
    // 0 = integrated candidate, 1 = immediately previous frame, 2 = the older
    // certified yield anchor.  An immediate rollback is normally enough.  The
    // anchor is used only when both one-frame rollbacks still leave a geometric
    // pinch, which is the equivalent of one pedestrian taking a small step
    // back so the stable-priority pedestrian can pass.
    TArray<uint8> ResolutionModes;
    ResolutionModes.SetNumZeroed(Records.Num());
    const auto GetResolvedPosition = [&Records, &ResolutionModes](
        const int32 RecordIndex)
    {
        const FCentralCertifiedFrameRecord& Record = Records[RecordIndex];
        if (ResolutionModes[RecordIndex] == 2)
        {
            return Record.YieldAnchorState.Transform.GetLocation();
        }
        if (ResolutionModes[RecordIndex] == 1)
        {
            return Record.PreviousState.Transform.GetLocation();
        }
        return Record.CandidateState.Transform.GetLocation();
    };
    for (int32 ConvergencePass = 0;
         ConvergencePass < Records.Num();
         ++ConvergencePass)
    {
        bool bAddedRollback = false;
        for (int32 FirstIndex = 0;
             FirstIndex < Records.Num() - 1;
             ++FirstIndex)
        {
            for (int32 SecondIndex = FirstIndex + 1;
                 SecondIndex < Records.Num();
                 ++SecondIndex)
            {
                const FCentralCertifiedFrameRecord& First =
                    Records[FirstIndex];
                const FCentralCertifiedFrameRecord& Second =
                    Records[SecondIndex];
                if (First.Spawner != Second.Spawner)
                {
                    continue;
                }
                const FVector FirstPosition = GetResolvedPosition(FirstIndex);
                const FVector SecondPosition = GetResolvedPosition(SecondIndex);
                const float ResolvedDistanceSquared = FVector::DistSquared(
                    FirstPosition,
                    SecondPosition);
                if (ResolvedDistanceSquared >=
                    CentralHardCollisionDistanceSquared)
                {
                    continue;
                }

                // A pair can already be inside the hard gate after a Cesium
                // presentation band temporarily falls back to the certified
                // centre line. Requiring either pedestrian to clear the full
                // 20 cm in one frame makes that overlap an absorbing state:
                // every small separating step is rolled back forever. Allow
                // only strictly monotonic separation from such a pre-existing
                // state. New overlaps and any step that holds or reduces the
                // distance still take the ordinary rollback/yield path below.
                const float PreviousPairDistanceSquared =
                    FVector::DistSquared(
                        First.PreviousState.Transform.GetLocation(),
                        Second.PreviousState.Transform.GetLocation());
                constexpr float CentralSeparationProgressSquaredEpsilon =
                    0.01f;
                if (PreviousPairDistanceSquared <
                        CentralHardCollisionDistanceSquared &&
                    ResolvedDistanceSquared >
                        PreviousPairDistanceSquared +
                            CentralSeparationProgressSquaredEpsilon)
                {
                    continue;
                }

                const bool bCanRollbackFirst =
                    ResolutionModes[FirstIndex] == 0 &&
                    (FVector::DistSquared(
                         First.PreviousState.Transform.GetLocation(),
                         SecondPosition) >=
                         CentralHardCollisionDistanceSquared ||
                     (PreviousPairDistanceSquared <
                          CentralHardCollisionDistanceSquared &&
                      FVector::DistSquared(
                          First.PreviousState.Transform.GetLocation(),
                          SecondPosition) >
                          PreviousPairDistanceSquared +
                              CentralSeparationProgressSquaredEpsilon));
                const bool bCanRollbackSecond =
                    ResolutionModes[SecondIndex] == 0 &&
                    (FVector::DistSquared(
                         FirstPosition,
                         Second.PreviousState.Transform.GetLocation()) >=
                         CentralHardCollisionDistanceSquared ||
                     (PreviousPairDistanceSquared <
                          CentralHardCollisionDistanceSquared &&
                      FVector::DistSquared(
                          FirstPosition,
                          Second.PreviousState.Transform.GetLocation()) >
                          PreviousPairDistanceSquared +
                              CentralSeparationProgressSquaredEpsilon));
                // Longest-waiting pedestrian owns the next safe movement. The
                // stable entity id is only a deterministic tie-breaker. Static
                // id priority starves the same high-index queue forever on a
                // short component that a low-index pedestrian traverses often.
                const bool bPreferFirstRollback =
                    !FMath::IsNearlyEqual(
                        First.StationarySeconds,
                        Second.StationarySeconds,
                        0.01f)
                    ? First.StationarySeconds < Second.StationarySeconds
                    : First.EntityIndex > Second.EntityIndex;
                if (bPreferFirstRollback && bCanRollbackFirst)
                {
                    ResolutionModes[FirstIndex] = 1;
                    bAddedRollback = true;
                }
                else if (!bPreferFirstRollback && bCanRollbackSecond)
                {
                    ResolutionModes[SecondIndex] = 1;
                    bAddedRollback = true;
                }
                else if (bCanRollbackFirst)
                {
                    ResolutionModes[FirstIndex] = 1;
                    bAddedRollback = true;
                }
                else if (bCanRollbackSecond)
                {
                    ResolutionModes[SecondIndex] = 1;
                    bAddedRollback = true;
                }
                else
                {
                    const int32 PreferredYieldIndex = bPreferFirstRollback
                        ? FirstIndex
                        : SecondIndex;
                    const int32 AlternateYieldIndex = bPreferFirstRollback
                        ? SecondIndex
                        : FirstIndex;
                    const auto TryUseYieldAnchorCascade = [
                        &Records,
                        &ResolutionModes,
                        CentralHardCollisionDistanceSquared](
                            const int32 YieldIndex,
                            const int32 ProtectedWinnerIndex)
                    {
                        TArray<uint8> TrialModes = ResolutionModes;
                        TArray<uint8> CascadeRecords;
                        CascadeRecords.SetNumZeroed(Records.Num());
                        TArray<int32> PendingRecords;
                        PendingRecords.Add(YieldIndex);
                        const auto GetTrialPosition = [
                            &Records,
                            &TrialModes](const int32 RecordIndex)
                        {
                            if (TrialModes[RecordIndex] == 2)
                            {
                                return Records[RecordIndex].YieldAnchorState.
                                    Transform.GetLocation();
                            }
                            if (TrialModes[RecordIndex] == 1)
                            {
                                return Records[RecordIndex].PreviousState.
                                    Transform.GetLocation();
                            }
                            return Records[RecordIndex].CandidateState.
                                Transform.GetLocation();
                        };

                        while (!PendingRecords.IsEmpty())
                        {
                            const int32 CascadeIndex = PendingRecords.Pop();
                            if (CascadeIndex == ProtectedWinnerIndex ||
                                !Records[CascadeIndex].
                                    bYieldAnchorNavigationValid ||
                                !Records[CascadeIndex].YieldAnchorState.bValid)
                            {
                                return false;
                            }
                            TrialModes[CascadeIndex] = 2;
                            CascadeRecords[CascadeIndex] = 1;

                            for (int32 FirstTrialIndex = 0;
                                 FirstTrialIndex < Records.Num() - 1;
                                 ++FirstTrialIndex)
                            {
                                for (int32 SecondTrialIndex =
                                         FirstTrialIndex + 1;
                                     SecondTrialIndex < Records.Num();
                                     ++SecondTrialIndex)
                                {
                                    if (Records[FirstTrialIndex].Spawner !=
                                            Records[SecondTrialIndex].Spawner ||
                                        (CascadeRecords[FirstTrialIndex] == 0 &&
                                         CascadeRecords[SecondTrialIndex] == 0) ||
                                        FVector::DistSquared(
                                            GetTrialPosition(FirstTrialIndex),
                                            GetTrialPosition(SecondTrialIndex)) >=
                                            CentralHardCollisionDistanceSquared)
                                    {
                                        continue;
                                    }

                                    const bool bFirstInCascade =
                                        CascadeRecords[FirstTrialIndex] != 0;
                                    const bool bSecondInCascade =
                                        CascadeRecords[SecondTrialIndex] != 0;
                                    if (bFirstInCascade && bSecondInCascade)
                                    {
                                        // Two historical anchors overlap, so
                                        // this queue has no certified space to
                                        // back into as a group.
                                        return false;
                                    }
                                    const int32 BlockingIndex = bFirstInCascade
                                        ? SecondTrialIndex
                                        : FirstTrialIndex;
                                    if (BlockingIndex == ProtectedWinnerIndex ||
                                        !Records[BlockingIndex].
                                            bYieldAnchorNavigationValid ||
                                        !Records[BlockingIndex].
                                            YieldAnchorState.bValid)
                                    {
                                        return false;
                                    }
                                    PendingRecords.AddUnique(BlockingIndex);
                                }
                            }
                        }

                        ResolutionModes = MoveTemp(TrialModes);
                        return true;
                    };
                    if (TryUseYieldAnchorCascade(
                            PreferredYieldIndex,
                            AlternateYieldIndex))
                    {
                        bAddedRollback = true;
                    }
                    else if (TryUseYieldAnchorCascade(
                                 AlternateYieldIndex,
                                 PreferredYieldIndex))
                    {
                        bAddedRollback = true;
                    }
                    else
                    {
                        if (ResolutionModes[FirstIndex] == 0)
                        {
                            ResolutionModes[FirstIndex] = 1;
                            bAddedRollback = true;
                        }
                        if (ResolutionModes[SecondIndex] == 0)
                        {
                            ResolutionModes[SecondIndex] = 1;
                            bAddedRollback = true;
                        }
                    }
                }
            }
        }
        if (!bAddedRollback)
        {
            break;
        }
    }

    for (int32 RecordIndex = 0;
         RecordIndex < Records.Num();
         ++RecordIndex)
    {
        FCentralCertifiedFrameRecord& Record = Records[RecordIndex];
        if (!EntityManager.IsEntityValid(Record.Entity) ||
            !Record.Spawner->RuntimeCentralPreviousFrameStates.IsValidIndex(
                Record.EntityIndex))
        {
            continue;
        }

        FTransformFragment& Transform =
            EntityManager.GetFragmentDataChecked<FTransformFragment>(
                Record.Entity);
        FMassZoneGraphLaneLocationFragment& Lane =
            EntityManager.GetFragmentDataChecked<
                FMassZoneGraphLaneLocationFragment>(Record.Entity);
        FMassVelocityFragment& Velocity =
            EntityManager.GetFragmentDataChecked<FMassVelocityFragment>(
                Record.Entity);
        FMassZoneGraphShortPathFragment& ShortPath =
            EntityManager.GetFragmentDataChecked<
                FMassZoneGraphShortPathFragment>(Record.Entity);
        FMassMoveTargetFragment& MoveTarget =
            EntityManager.GetFragmentDataChecked<FMassMoveTargetFragment>(
                Record.Entity);
        if (ResolutionModes[RecordIndex] != 0)
        {
            const bool bUseYieldAnchor =
                ResolutionModes[RecordIndex] == 2;
            const AOpenMassCrowdSpawner::FLastValidGroundState& RestoredState =
                bUseYieldAnchor
                ? Record.YieldAnchorState
                : Record.PreviousState;
            Transform.SetTransform(RestoredState.Transform);
            const FZoneGraphLaneHandle IntegratedLaneHandle = Lane.LaneHandle;
            Lane.LaneHandle = RestoredState.LaneHandle;
            Lane.DistanceAlongLane = RestoredState.DistanceAlongLane;
            Lane.LaneLength = RestoredState.LaneLength;
            Velocity.Value = FVector::ZeroVector;
            if (bUseYieldAnchor && Record.bYieldAnchorNavigationValid)
            {
                ShortPath = Record.YieldAnchorShortPath;
                MoveTarget = Record.YieldAnchorMoveTarget;
            }
            else if (Record.bPreviousNavigationValid)
            {
                ShortPath = Record.PreviousShortPath;
                MoveTarget = Record.PreviousMoveTarget;
            }

            if (FMassCrowdLaneTrackingFragment* LaneTracking =
                    EntityManager.GetFragmentDataPtr<
                        FMassCrowdLaneTrackingFragment>(Record.Entity))
            {
                if (LaneTracking->TrackedLaneHandle != Lane.LaneHandle)
                {
                    if (UMassCrowdSubsystem* CrowdSubsystem =
                            UWorld::GetSubsystem<UMassCrowdSubsystem>(
                                Record.Spawner->GetWorld()))
                    {
                        CrowdSubsystem->OnEntityLaneChanged(
                            Record.Entity,
                            IntegratedLaneHandle,
                            Lane.LaneHandle);
                    }
                    LaneTracking->TrackedLaneHandle = Lane.LaneHandle;
                }
            }
        }

        AOpenMassCrowdSpawner::FLastValidGroundState& PreviousFrameState =
            Record.Spawner->RuntimeCentralPreviousFrameStates[
                Record.EntityIndex];
        PreviousFrameState.Transform = Transform.GetTransform();
        PreviousFrameState.LaneHandle = Lane.LaneHandle;
        PreviousFrameState.DistanceAlongLane = Lane.DistanceAlongLane;
        PreviousFrameState.LaneLength = Lane.LaneLength;
        PreviousFrameState.bValid = true;
        Record.Spawner->RuntimeCentralPreviousFrameShortPaths[
            Record.EntityIndex] = ShortPath;
        Record.Spawner->RuntimeCentralPreviousFrameMoveTargets[
            Record.EntityIndex] = MoveTarget;
        Record.Spawner->RuntimeCentralPreviousFrameNavigationValid[
            Record.EntityIndex] = 1;

        AOpenMassCrowdSpawner::FLastValidGroundState& YieldAnchorState =
            Record.Spawner->RuntimeCentralYieldAnchorStates[
                Record.EntityIndex];
        constexpr float CentralYieldAnchorSpacingCm = 80.0f;
        if (!YieldAnchorState.bValid ||
            FVector::DistSquared(
                YieldAnchorState.Transform.GetLocation(),
                Transform.GetTransform().GetLocation()) >=
                FMath::Square(CentralYieldAnchorSpacingCm))
        {
            YieldAnchorState = PreviousFrameState;
            Record.Spawner->RuntimeCentralYieldAnchorShortPaths[
                Record.EntityIndex] = ShortPath;
            Record.Spawner->RuntimeCentralYieldAnchorMoveTargets[
                Record.EntityIndex] = MoveTarget;
            Record.Spawner->RuntimeCentralYieldAnchorNavigationValid[
                Record.EntityIndex] = 1;
        }
    }
}

void UOpenMassCrowdVATPlaybackTrait::BuildTemplate(
    FMassEntityTemplateBuildContext& BuildContext,
    const UWorld& World) const
{
    FOpenMassCrowdVATPlaybackFragment& Playback =
        BuildContext.AddFragment_GetRef<FOpenMassCrowdVATPlaybackFragment>();
    Playback.StartFrame = StartFrame;
    Playback.EndFrame = EndFrame;
}

UOpenMassCrowdVATCustomDataProcessor::UOpenMassCrowdVATCustomDataProcessor()
    : EntityQuery(*this)
{
    ExecutionFlags = static_cast<int32>(
        EProcessorExecutionFlags::Client | EProcessorExecutionFlags::Standalone);
    bAutoRegisterWithProcessingPhases = true;
    bRequiresGameThreadExecution = true;

    ExecutionOrder.ExecuteInGroup = UE::Mass::ProcessorGroupNames::Representation;
    ExecutionOrder.ExecuteAfter.Add(UMassCrowdVisualizationProcessor::StaticClass()->GetFName());
    ExecutionOrder.ExecuteBefore.Add(UMassUpdateISMProcessor::StaticClass()->GetFName());
}

void UOpenMassCrowdVATCustomDataProcessor::ConfigureQueries(
    const TSharedRef<FMassEntityManager>& EntityManager)
{
    EntityQuery.AddTagRequirement<FOpenMassCrowdTag>(EMassFragmentPresence::All);
    EntityQuery.AddRequirement<FOpenMassCrowdVATPlaybackFragment>(EMassFragmentAccess::ReadWrite);
    EntityQuery.AddRequirement<FMassRepresentationFragment>(EMassFragmentAccess::ReadOnly);
    EntityQuery.AddRequirement<FMassRepresentationLODFragment>(EMassFragmentAccess::ReadOnly);
    EntityQuery.AddChunkRequirement<FMassVisualizationChunkFragment>(EMassFragmentAccess::ReadOnly);
    EntityQuery.SetChunkFilter(&FMassVisualizationChunkFragment::AreAnyEntitiesVisibleInChunk);
    EntityQuery.AddSharedRequirement<FMassRepresentationSubsystemSharedFragment>(EMassFragmentAccess::ReadWrite);
}

void UOpenMassCrowdVATCustomDataProcessor::Execute(
    FMassEntityManager& EntityManager,
    FMassExecutionContext& Context)
{
    int32 BatchedEntityCount = 0;
    EntityQuery.ForEachEntityChunk(Context, [&BatchedEntityCount](FMassExecutionContext& Context)
    {
        UMassRepresentationSubsystem* RepresentationSubsystem =
            Context.GetMutableSharedFragment<FMassRepresentationSubsystemSharedFragment>()
                .RepresentationSubsystem;
        check(RepresentationSubsystem);

        FMassInstancedStaticMeshInfoArrayView ISMInfos =
            RepresentationSubsystem->GetMutableInstancedStaticMeshInfos();
        const TArrayView<FOpenMassCrowdVATPlaybackFragment> PlaybackList =
            Context.GetMutableFragmentView<FOpenMassCrowdVATPlaybackFragment>();
        const TConstArrayView<FMassRepresentationFragment> RepresentationList =
            Context.GetFragmentView<FMassRepresentationFragment>();
        const TConstArrayView<FMassRepresentationLODFragment> RepresentationLODList =
            Context.GetFragmentView<FMassRepresentationLODFragment>();

        for (FMassExecutionContext::FEntityIterator EntityIt = Context.CreateEntityIterator();
             EntityIt;
             ++EntityIt)
        {
            const FMassRepresentationFragment& Representation = RepresentationList[EntityIt];
            if (Representation.CurrentRepresentation != EMassRepresentationType::StaticMeshInstance)
            {
                continue;
            }

            const int32 ISMInfoIndex = Representation.StaticMeshDescHandle.ToIndex();
            if (!ensureMsgf(
                ISMInfos.IsValidIndex(ISMInfoIndex),
                TEXT("Invalid OpenMassCrowd ISM handle index %d"),
                ISMInfoIndex))
            {
                continue;
            }

            FOpenMassCrowdVATPlaybackFragment& Playback = PlaybackList[EntityIt];
            // The previous fixed 0.9..1.1 rate made a 98-frame walk clip read
            // as a nearly rigid sliding silhouette at medium/far distances.
            // Keep one phase-continuous rate: changing PlayRate against the
            // shader's absolute world time causes visible frame jumps. 1.35x
            // keeps the gait legible at the 60-120 m demo view without that
            // discontinuity.
            Playback.PlayRate = 1.35f;
            FAnimToTextureAutoPlayData AutoPlayData;
            AutoPlayData.TimeOffset = Playback.TimeOffset;
            AutoPlayData.PlayRate = Playback.PlayRate;
            AutoPlayData.StartFrame = Playback.StartFrame;
            AutoPlayData.EndFrame = Playback.EndFrame;

            const FMassRepresentationLODFragment& RepresentationLOD =
                RepresentationLODList[EntityIt];
            ISMInfos[ISMInfoIndex].AddBatchedCustomData(
                AutoPlayData,
                RepresentationLOD.LODSignificance,
                Representation.PrevLODSignificance);
            ++BatchedEntityCount;
        }
    });

    if (!bLoggedFirstVATBatch && BatchedEntityCount > 0)
    {
        bLoggedFirstVATBatch = true;
        UE_LOG(
            LogTemp,
            Log,
            TEXT("OPEN_MASS_CROWD_VAT_BATCH_READY entities=%d custom_floats=4"),
            BatchedEntityCount);
    }
}
