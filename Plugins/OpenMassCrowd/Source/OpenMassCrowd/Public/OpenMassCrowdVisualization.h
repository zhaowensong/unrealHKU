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
class AActor;
class AOpenMassCrowdSpawner;

/**
 * Caps Central follower speed before UE advances ZoneGraph path progress.
 * This is a reservation/headway rule, not a post-movement depenetration pass:
 * Local mode is excluded and certified lane positions remain authoritative.
 */
UCLASS(meta = (DisplayName = "Open Mass Crowd Pre Path Follow Spacing"))
class OPENMASSCROWD_API UOpenMassCrowdPrePathFollowSpacingProcessor final
    : public UMassProcessor
{
    GENERATED_BODY()

public:
    UOpenMassCrowdPrePathFollowSpacingProcessor();

protected:
    virtual void ConfigureQueries(
        const TSharedRef<FMassEntityManager>& EntityManager) override;
    virtual void Execute(
        FMassEntityManager& EntityManager,
        FMassExecutionContext& Context) override;

private:
    FMassEntityQuery EntityQuery;
    TMap<TWeakObjectPtr<AOpenMassCrowdSpawner>, TMap<FName, FMassEntityHandle>>
        RuntimeCentralNodeOwners;
};

/**
 * Enforces the Central reservation cap on the final desired velocity after
 * avoidance/force accumulation and immediately before Mass integrates movement.
 */
UCLASS(meta = (DisplayName = "Open Mass Crowd Post Avoidance Reservation"))
class OPENMASSCROWD_API UOpenMassCrowdPostAvoidanceReservationProcessor final
    : public UMassProcessor
{
    GENERATED_BODY()

public:
    UOpenMassCrowdPostAvoidanceReservationProcessor();

protected:
    virtual void ConfigureQueries(
        const TSharedRef<FMassEntityManager>& EntityManager) override;
    virtual void Execute(
        FMassEntityManager& EntityManager,
        FMassExecutionContext& Context) override;

private:
    FMassEntityQuery EntityQuery;
};

/**
 * Applies the cached Cesium-certified lane transform after Mass movement and
 * avoidance but before representation/ISM submission. This closes the frame-
 * ordering gap that an Actor PostUpdateWork tick cannot cover for VAT meshes.
 */
UCLASS(meta = (DisplayName = "Open Mass Crowd Certified Transform"))
class OPENMASSCROWD_API UOpenMassCrowdCertifiedTransformProcessor final
    : public UMassProcessor
{
    GENERATED_BODY()

public:
    UOpenMassCrowdCertifiedTransformProcessor();

protected:
    virtual void ConfigureQueries(
        const TSharedRef<FMassEntityManager>& EntityManager) override;
    virtual void Execute(
        FMassEntityManager& EntityManager,
        FMassExecutionContext& Context) override;

private:
    FMassEntityQuery EntityQuery;
};

/**
 * One independently baked mesh in a modular City Sample VAT appearance.
 *
 * City Sample crowd characters are assembled from clothing, shoes and a head;
 * their ``*_base`` skeletal meshes are animation-driver triangles rather than
 * visible bodies.  Keeping the parts in one Mass visualization descriptor
 * lets every part receive the same entity transform and playback phase while
 * retaining real Epic-authored geometry.
 */
USTRUCT(BlueprintType)
struct OPENMASSCROWD_API FOpenMassCrowdVATPartConfig
{
    GENERATED_BODY()

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Open Mass Crowd|Visual")
    FName PartName = NAME_None;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Open Mass Crowd|Visual")
    TSoftObjectPtr<UStaticMesh> StaticMesh;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Open Mass Crowd|Visual")
    TArray<TSoftObjectPtr<UMaterialInterface>> MaterialOverrides;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Open Mass Crowd|Visual")
    TSoftObjectPtr<UAnimToTextureDataAsset> AnimationData;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Open Mass Crowd|Visual")
    TSoftObjectPtr<UAnimSequence> AnimationSequence;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Open Mass Crowd|Visual")
    FTransform LocalTransform = FTransform::Identity;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Open Mass Crowd|Visual")
    bool bCastShadows = true;
};

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

    /**
     * Optional official City Sample modular assembly.  When populated, every
     * valid part is emitted into the same Mass ISM descriptor and the legacy
     * single-mesh fields above are ignored for VAT rendering.  They remain for
     * byte-compatible loading of the proven local 30-person fallback.
     */
    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Open Mass Crowd|Visual")
    TArray<FOpenMassCrowdVATPartConfig> VATParts;

    /**
     * Enables Mass spawned-actor representation for this variant. Actor class
     * references are deliberately ignored while this is false, preserving the
     * default VAT-only mannequin path.
     */
    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Open Mass Crowd|Visual")
    bool bUseActorRepresentation = false;

    /** Actor used by the High representation LOD. Falls back to LowResTemplateActor when unset. */
    UPROPERTY(
        EditAnywhere,
        BlueprintReadWrite,
        Category = "Open Mass Crowd|Visual",
        meta = (EditCondition = "bUseActorRepresentation"))
    TSoftClassPtr<AActor> HighResTemplateActor;

    /** Actor used by the Medium/Low representation LODs. Falls back to HighResTemplateActor when unset. */
    UPROPERTY(
        EditAnywhere,
        BlueprintReadWrite,
        Category = "Open Mass Crowd|Visual",
        meta = (EditCondition = "bUseActorRepresentation"))
    TSoftClassPtr<AActor> LowResTemplateActor;

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
