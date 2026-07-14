#pragma once

#include "MassEntityTypes.h"
#include "MassEntityTraitBase.h"

#include "OpenMassCrowdTrait.generated.h"

USTRUCT()
struct FOpenMassCrowdTag : public FMassTag
{
    GENERATED_BODY()
};

UCLASS(meta = (DisplayName = "Open Mass Crowd Base"))
class OPENMASSCROWD_API UOpenMassCrowdTrait final : public UMassEntityTraitBase
{
    GENERATED_BODY()

protected:
    virtual void BuildTemplate(
        FMassEntityTemplateBuildContext& BuildContext,
        const UWorld& World) const override;
};
