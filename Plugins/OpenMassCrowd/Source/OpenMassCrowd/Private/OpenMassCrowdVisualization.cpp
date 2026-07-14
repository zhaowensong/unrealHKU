#include "OpenMassCrowdVisualization.h"

#include "OpenMassCrowdTrait.h"

#include "AnimToTextureInstancePlaybackHelpers.h"
#include "MassCrowdVisualizationProcessor.h"
#include "MassExecutionContext.h"
#include "MassLODFragments.h"
#include "MassRepresentationFragments.h"
#include "MassRepresentationSubsystem.h"
#include "MassRepresentationTypes.h"
#include "MassUpdateISMProcessor.h"
#include "MassEntityTemplateRegistry.h"

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
    EntityQuery.AddRequirement<FOpenMassCrowdVATPlaybackFragment>(EMassFragmentAccess::ReadOnly);
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
        const TConstArrayView<FOpenMassCrowdVATPlaybackFragment> PlaybackList =
            Context.GetFragmentView<FOpenMassCrowdVATPlaybackFragment>();
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

            const FOpenMassCrowdVATPlaybackFragment& Playback = PlaybackList[EntityIt];
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
