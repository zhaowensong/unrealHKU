#pragma once

#include "MassEntityTypes.h"
#include "MassEntityTraitBase.h"

#include "OpenMassCrowdTrait.generated.h"

class AOpenMassCrowdSpawner;

USTRUCT()
struct FOpenMassCrowdTag : public FMassTag
{
    GENERATED_BODY()
};

/**
 * Runtime-only owner/index used by the pre-representation certified-transform
 * processor. Local entities leave this empty and retain their proven path.
 */
USTRUCT()
struct FOpenMassCrowdCentralVisualOwnerFragment : public FMassFragment
{
    GENERATED_BODY()

    TWeakObjectPtr<AOpenMassCrowdSpawner> Spawner;
    int32 EntityIndex = INDEX_NONE;
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
