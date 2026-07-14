#include "OpenMassCrowdTrait.h"

#include "MassActorSubsystem.h"
#include "MassCommonFragments.h"
#include "MassEntityTemplateRegistry.h"

void UOpenMassCrowdTrait::BuildTemplate(
    FMassEntityTemplateBuildContext& BuildContext,
    const UWorld& World) const
{
    BuildContext.AddTag<FOpenMassCrowdTag>();
    BuildContext.AddFragment<FTransformFragment>();
    BuildContext.AddFragment<FAgentRadiusFragment>();
    BuildContext.AddFragment<FMassActorFragment>();
}
