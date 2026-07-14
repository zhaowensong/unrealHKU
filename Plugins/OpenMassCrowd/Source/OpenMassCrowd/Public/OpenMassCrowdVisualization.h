#pragma once

#include "CoreMinimal.h"
#include "MassEntityQuery.h"
#include "MassEntityTraitBase.h"
#include "MassProcessor.h"

#include "OpenMassCrowdVisualization.generated.h"

class UAnimSequence;
class UAnimToTextureDataAsset;
class UMaterialInterface;
class UStaticMesh;

/**
 * All asset references for one crowd appearance live here so the temporary
 * engine mannequin can be replaced by migrated City Sample VAT assets without
 * changing the Mass spawning or movement code.
 */
USTRUCT(BlueprintType)
struct OPENMASSCROWD_API FOpenMassCrowdVisualConfig
{
    GENERATED_BODY()

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Open Mass Crowd|Visual")
    FName VariantName = NAME_None;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Open Mass Crowd|Visual")
    TSoftObjectPtr<UStaticMesh> StaticMesh;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Open Mass Crowd|Visual")
    TArray<TSoftObjectPtr<UMaterialInterface>> MaterialOverrides;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Open Mass Crowd|Visual")
    TSoftObjectPtr<UAnimToTextureDataAsset> AnimationData;

    /** Sequence is resolved through the data asset; no baked animation index is assumed. */
    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Open Mass Crowd|Visual")
    TSoftObjectPtr<UAnimSequence> AnimationSequence;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Open Mass Crowd|Visual")
    FTransform LocalTransform = FTransform::Identity;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Open Mass Crowd|Visual")
    bool bCastShadows = true;
};

/** Per-entity AnimToTexture auto-play values consumed by the VAT ISM processor. */
USTRUCT()
struct FOpenMassCrowdVATPlaybackFragment : public FMassFragment
{
    GENERATED_BODY()

    float TimeOffset = 0.0f;
    float PlayRate = 1.0f;
    float StartFrame = 0.0f;
    float EndFrame = 1.0f;
};

/** Adds initialized VAT playback data to a runtime-created Mass template. */
UCLASS(meta = (DisplayName = "Open Mass Crowd VAT Playback"))
class OPENMASSCROWD_API UOpenMassCrowdVATPlaybackTrait final : public UMassEntityTraitBase
{
    GENERATED_BODY()

public:
    float StartFrame = 0.0f;
    float EndFrame = 1.0f;

protected:
    virtual void BuildTemplate(
        FMassEntityTemplateBuildContext& BuildContext,
        const UWorld& World) const override;
};

/**
 * Appends the four floats expected by AnimToTexture autoplay materials to the
 * same batched ISM update that Mass uses for transforms.
 */
UCLASS(meta = (DisplayName = "Open Mass Crowd VAT Custom Data"))
class OPENMASSCROWD_API UOpenMassCrowdVATCustomDataProcessor final : public UMassProcessor
{
    GENERATED_BODY()

public:
    UOpenMassCrowdVATCustomDataProcessor();

protected:
    virtual void ConfigureQueries(const TSharedRef<FMassEntityManager>& EntityManager) override;
    virtual void Execute(FMassEntityManager& EntityManager, FMassExecutionContext& Context) override;

private:
    FMassEntityQuery EntityQuery;
    bool bLoggedFirstVATBatch = false;
};
