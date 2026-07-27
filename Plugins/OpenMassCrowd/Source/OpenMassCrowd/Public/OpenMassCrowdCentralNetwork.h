#pragma once

#include "CoreMinimal.h"
#include "Engine/DataAsset.h"

#include "OpenMassCrowdCentralNetwork.generated.h"

/**
 * UE serialization version for Central network assets. The reflected
 * SchemaVersion properties remain visible to converters and audit tools,
 * while this custom version protects binary .uasset serialization.
 */
struct OPENMASSCROWD_API FOpenMassCrowdCentralNetworkCustomVersion
{
    static const FGuid GUID;

    enum Type : int32
    {
        BeforeCustomVersionWasAdded = 0,
        InitialVersion = 1,
        CertifiedComponentPartitions = 2,
        GroundOnlyEligibility = 3,
        LatestVersion = GroundOnlyEligibility
    };
};

/** How the source pedestrian topology entered the reviewed network. */
UENUM(BlueprintType)
enum class EOpenMassCrowdCentralTopologyOrigin : uint8
{
    Unknown UMETA(DisplayName = "Unknown / Rejected"),
    OpenStreetMap UMETA(DisplayName = "OpenStreetMap"),
    OsmSemanticRecovery UMETA(DisplayName = "OSM Semantic Recovery"),
    ManualCorrection UMETA(DisplayName = "Manual Correction"),
    GeneratedConnector UMETA(DisplayName = "Generated Connector")
};

/** Permitted pedestrian semantics carried from the source topology. */
UENUM(BlueprintType)
enum class EOpenMassCrowdCentralPedestrianClass : uint8
{
    Unknown UMETA(DisplayName = "Unknown / Not Admitted"),
    Sidewalk UMETA(DisplayName = "Sidewalk"),
    Footway UMETA(DisplayName = "Footway"),
    Crossing UMETA(DisplayName = "Crossing"),
    PedestrianZone UMETA(DisplayName = "Pedestrian Zone"),
    ManualPedestrianLink UMETA(DisplayName = "Manual Pedestrian Link")
};

/** Runtime-relevant role of a certified network node. */
UENUM(BlueprintType)
enum class EOpenMassCrowdCentralNodeKind : uint8
{
    Endpoint,
    Waypoint,
    Junction,
    CrossingEndpoint,
    Portal,
    SpawnAnchor
};

/** Evidence bits that every stored ground sample is expected to carry. */
UENUM(BlueprintType, meta = (Bitflags, UseEnumValuesAsMaskValuesInEditor = "true"))
enum class EOpenMassCrowdCentralGroundEvidence : uint8
{
    None = 0 UMETA(Hidden),
    ExactXYSupport = 1 << 0,
    FirstBlocker = 1 << 1,
    HeightContinuity = 1 << 2,
    Slope = 1 << 3,
    MultiTrackSupport = 1 << 4,
    CapsuleClearance = 1 << 5
};
ENUM_CLASS_FLAGS(EOpenMassCrowdCentralGroundEvidence);

/**
 * Compatibility and content hashes for a network or independently rebuildable
 * cell. Hashes are stored as text so the UE asset and JSON audit mirror use
 * the exact same values.
 */
USTRUCT(BlueprintType)
struct OPENMASSCROWD_API FOpenMassCrowdCentralNetworkHashes
{
    GENERATED_BODY()

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Open Mass Crowd|Central|Hash")
    FString HashAlgorithm = TEXT("SHA-256");

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Open Mass Crowd|Central|Hash")
    FString TopologySha256;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Open Mass Crowd|Central|Hash")
    FString GeoreferenceSha256;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Open Mass Crowd|Central|Hash")
    FString TilesetSha256;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Open Mass Crowd|Central|Hash")
    FString CollisionSettingsSha256;

    /** Hash of this cell's complete serialized certified payload. */
    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Open Mass Crowd|Central|Hash")
    FString CellContentSha256;

    /** Canonical aggregate of all applicable input and content hashes. */
    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Open Mass Crowd|Central|Hash")
    FString CombinedSha256;

    bool HasRequiredCompatibilityInputs() const;
};

/** Machine-readable counters produced by certification and coverage checks. */
USTRUCT(BlueprintType)
struct OPENMASSCROWD_API FOpenMassCrowdCentralCertificationEvidence
{
    GENERATED_BODY()

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Open Mass Crowd|Central|Evidence")
    int32 CandidateLaneCount = 0;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Open Mass Crowd|Central|Evidence")
    int32 CertifiedLaneCount = 0;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Open Mass Crowd|Central|Evidence")
    int32 RejectedLaneCount = 0;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Open Mass Crowd|Central|Evidence")
    int32 CoarseSupportCheckCount = 0;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Open Mass Crowd|Central|Evidence")
    int32 StrictGroundSampleCount = 0;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Open Mass Crowd|Central|Evidence")
    int32 ExactXYSupportPassCount = 0;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Open Mass Crowd|Central|Evidence")
    int32 FirstBlockerPassCount = 0;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Open Mass Crowd|Central|Evidence")
    int32 HeightContinuityPassCount = 0;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Open Mass Crowd|Central|Evidence")
    int32 SlopePassCount = 0;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Open Mass Crowd|Central|Evidence")
    int32 MultiTrackSupportPassCount = 0;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Open Mass Crowd|Central|Evidence")
    int32 CapsuleClearancePassCount = 0;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Open Mass Crowd|Central|Evidence|Rejected")
    int32 MissingSupportRejectionCount = 0;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Open Mass Crowd|Central|Evidence|Rejected")
    int32 FirstBlockerRejectionCount = 0;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Open Mass Crowd|Central|Evidence|Rejected")
    int32 HeightContinuityRejectionCount = 0;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Open Mass Crowd|Central|Evidence|Rejected")
    int32 SlopeRejectionCount = 0;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Open Mass Crowd|Central|Evidence|Rejected")
    int32 MultiTrackRejectionCount = 0;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Open Mass Crowd|Central|Evidence|Rejected")
    int32 CapsuleClearanceRejectionCount = 0;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Open Mass Crowd|Central|Evidence|Coverage")
    int32 ConnectedComponentCount = 0;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Open Mass Crowd|Central|Evidence|Coverage")
    int32 StreetBlockCount = 0;

    /** Configured geographic cells containing at least one certified runtime lane. */
    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Open Mass Crowd|Central|Evidence|Coverage")
    int32 CertifiedGeographicBlockCount = 0;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Open Mass Crowd|Central|Evidence|Coverage")
    int32 JunctionCount = 0;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Open Mass Crowd|Central|Evidence|Coverage")
    int32 PortalCount = 0;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Open Mass Crowd|Central|Evidence|Coverage")
    int32 SpawnDistrictCount = 0;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Open Mass Crowd|Central|Evidence|Coverage")
    double CertifiedDirectionalLaneLengthCm = 0.0;

    /** Must remain zero when a compatible cache is loaded during normal PIE. */
    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Open Mass Crowd|Central|Evidence|Runtime")
    int32 WholeAreaRecertificationCount = 0;
};

/** One strict exact-XY sample of an admitted directional pedestrian lane. */
USTRUCT(BlueprintType)
struct OPENMASSCROWD_API FOpenMassCrowdCentralGroundSample
{
    GENERATED_BODY()

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Open Mass Crowd|Central|Ground Sample")
    FName SampleId = NAME_None;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Open Mass Crowd|Central|Ground Sample")
    int32 SampleIndex = INDEX_NONE;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Open Mass Crowd|Central|Ground Sample")
    double DistanceAlongLaneCm = 0.0;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Open Mass Crowd|Central|Ground Sample")
    FVector CenterPosition = FVector::ZeroVector;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Open Mass Crowd|Central|Ground Sample")
    FVector LeftTrackPosition = FVector::ZeroVector;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Open Mass Crowd|Central|Ground Sample")
    FVector RightTrackPosition = FVector::ZeroVector;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Open Mass Crowd|Central|Ground Sample")
    FVector SurfaceNormal = FVector::UpVector;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Open Mass Crowd|Central|Ground Sample")
    float SurfaceSlopeDegrees = 0.0f;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Open Mass Crowd|Central|Ground Sample")
    float MaxNeighborHeightDeltaCm = 0.0f;

    /** Stable audit identifier for the Cesium primitive that supplied support. */
    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Open Mass Crowd|Central|Ground Sample")
    FName SupportingPrimitiveId = NAME_None;

    UPROPERTY(
        EditAnywhere,
        BlueprintReadWrite,
        Category = "Open Mass Crowd|Central|Ground Sample",
        meta = (Bitmask, BitmaskEnum = "/Script/OpenMassCrowd.EOpenMassCrowdCentralGroundEvidence"))
    int32 EvidenceMask = 0;
};

/** Stable graph node stored inside one independently rebuildable cell. */
USTRUCT(BlueprintType)
struct OPENMASSCROWD_API FOpenMassCrowdCentralNode
{
    GENERATED_BODY()

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Open Mass Crowd|Central|Node")
    FName NodeId = NAME_None;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Open Mass Crowd|Central|Node")
    FName CellId = NAME_None;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Open Mass Crowd|Central|Node")
    FName ComponentId = NAME_None;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Open Mass Crowd|Central|Node")
    EOpenMassCrowdCentralNodeKind Kind = EOpenMassCrowdCentralNodeKind::Waypoint;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Open Mass Crowd|Central|Node")
    FVector Position = FVector::ZeroVector;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Open Mass Crowd|Central|Node")
    TArray<FName> IncomingLaneIds;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Open Mass Crowd|Central|Node")
    TArray<FName> OutgoingLaneIds;
};

/** One certified, one-way pedestrian lane between two stable nodes. */
USTRUCT(BlueprintType)
struct OPENMASSCROWD_API FOpenMassCrowdCentralDirectedLane
{
    GENERATED_BODY()

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Open Mass Crowd|Central|Lane")
    FName LaneId = NAME_None;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Open Mass Crowd|Central|Lane")
    FName CellId = NAME_None;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Open Mass Crowd|Central|Lane")
    FName ComponentId = NAME_None;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Open Mass Crowd|Central|Lane")
    FName SourceFeatureId = NAME_None;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Open Mass Crowd|Central|Lane")
    FName FromNodeId = NAME_None;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Open Mass Crowd|Central|Lane")
    FName ToNodeId = NAME_None;

    /** Opposite directional lane, used to apply the anti-U-turn cost. */
    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Open Mass Crowd|Central|Lane")
    FName ReverseLaneId = NAME_None;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Open Mass Crowd|Central|Lane")
    EOpenMassCrowdCentralTopologyOrigin TopologyOrigin = EOpenMassCrowdCentralTopologyOrigin::Unknown;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Open Mass Crowd|Central|Lane")
    EOpenMassCrowdCentralPedestrianClass PedestrianClass = EOpenMassCrowdCentralPedestrianClass::Unknown;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Open Mass Crowd|Central|Lane", meta = (ClampMin = "1.0"))
    float WidthCm = 120.0f;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Open Mass Crowd|Central|Lane", meta = (ClampMin = "0.0"))
    double LengthCm = 0.0;

    /** Fail-closed admission bit; runtime graph builders must ignore false lanes. */
    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Open Mass Crowd|Central|Lane")
    bool bCertified = false;

    /** Explicit semantic provenance gate; false lanes must never enter Ground-Only runtime pools. */
    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Open Mass Crowd|Central|Lane")
    bool bGroundOnlyEligible = false;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Open Mass Crowd|Central|Lane")
    TArray<FOpenMassCrowdCentralGroundSample> GroundSamples;
};

/**
 * One directed exit from a local cell to a neighboring cell. A bidirectional
 * connection is represented by a second portal with ReversePortalId set.
 */
USTRUCT(BlueprintType)
struct OPENMASSCROWD_API FOpenMassCrowdCentralPortal
{
    GENERATED_BODY()

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Open Mass Crowd|Central|Portal")
    FName PortalId = NAME_None;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Open Mass Crowd|Central|Portal")
    FName ReversePortalId = NAME_None;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Open Mass Crowd|Central|Portal")
    FName LocalCellId = NAME_None;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Open Mass Crowd|Central|Portal")
    FName RemoteCellId = NAME_None;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Open Mass Crowd|Central|Portal")
    FName LocalNodeId = NAME_None;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Open Mass Crowd|Central|Portal")
    FName RemoteNodeId = NAME_None;

    /** Certified directional lane that traverses this portal. */
    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Open Mass Crowd|Central|Portal")
    FName DirectedLaneId = NAME_None;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Open Mass Crowd|Central|Portal")
    FVector Position = FVector::ZeroVector;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Open Mass Crowd|Central|Portal")
    bool bCertified = false;
};

/** One independently hashable and rebuildable Central network cell. */
USTRUCT(BlueprintType)
struct OPENMASSCROWD_API FOpenMassCrowdCentralCell
{
    GENERATED_BODY()

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Open Mass Crowd|Central|Cell")
    int32 SchemaVersion = FOpenMassCrowdCentralNetworkCustomVersion::LatestVersion;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Open Mass Crowd|Central|Cell")
    FName CellId = NAME_None;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Open Mass Crowd|Central|Cell")
    FIntPoint GridCoordinate = FIntPoint::ZeroValue;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Open Mass Crowd|Central|Cell")
    FBox WorldBounds = FBox(EForceInit::ForceInit);

    /** Stable source features included in this cell, including manual corrections. */
    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Open Mass Crowd|Central|Cell")
    TArray<FName> SourceFeatureIds;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Open Mass Crowd|Central|Cell")
    TArray<FOpenMassCrowdCentralNode> Nodes;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Open Mass Crowd|Central|Cell")
    TArray<FOpenMassCrowdCentralDirectedLane> DirectedLanes;

    /** Directed outbound links owned by this cell. */
    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Open Mass Crowd|Central|Cell")
    TArray<FOpenMassCrowdCentralPortal> Portals;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Open Mass Crowd|Central|Cell")
    FOpenMassCrowdCentralNetworkHashes Hashes;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Open Mass Crowd|Central|Cell")
    FOpenMassCrowdCentralCertificationEvidence Evidence;

    /** False for stale, incomplete, or unreviewed cells so they fail closed. */
    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Open Mass Crowd|Central|Cell")
    bool bCertified = false;
};

/** One independently connected, strictly certified routing island. */
USTRUCT(BlueprintType)
struct OPENMASSCROWD_API FOpenMassCrowdCentralComponent
{
    GENERATED_BODY()

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Open Mass Crowd|Central|Component")
    FName ComponentId = NAME_None;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Open Mass Crowd|Central|Component")
    TArray<FName> CellIds;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Open Mass Crowd|Central|Component")
    TArray<FName> NodeIds;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Open Mass Crowd|Central|Component")
    TArray<FName> DirectedLaneIds;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Open Mass Crowd|Central|Component")
    double DirectionalLaneLengthCm = 0.0;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Open Mass Crowd|Central|Component")
    int32 JunctionCount = 0;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Open Mass Crowd|Central|Component")
    int32 StreetBlockCount = 0;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Open Mass Crowd|Central|Component")
    bool bCertified = false;
};

/** Population allocation and valid admission points for one connected district. */
USTRUCT(BlueprintType)
struct OPENMASSCROWD_API FOpenMassCrowdCentralSpawnDistrict
{
    GENERATED_BODY()

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Open Mass Crowd|Central|Spawn District")
    FName DistrictId = NAME_None;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Open Mass Crowd|Central|Spawn District")
    FName ComponentId = NAME_None;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Open Mass Crowd|Central|Spawn District")
    TArray<FName> CellIds;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Open Mass Crowd|Central|Spawn District")
    TArray<FName> SpawnNodeIds;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Open Mass Crowd|Central|Spawn District")
    TArray<FName> SpawnLaneIds;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Open Mass Crowd|Central|Spawn District")
    FBox WorldBounds = FBox(EForceInit::ForceInit);

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Open Mass Crowd|Central|Spawn District", meta = (ClampMin = "0"))
    int32 TargetPopulation = 0;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Open Mass Crowd|Central|Spawn District", meta = (ClampMin = "0.0"))
    float SelectionWeight = 1.0f;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Open Mass Crowd|Central|Spawn District")
    bool bEnabled = true;
};

/** Versioned certified Central pedestrian network consumed by runtime mode. */
UCLASS(BlueprintType)
class OPENMASSCROWD_API UOpenMassCrowdCentralNetworkDataAsset final : public UDataAsset
{
    GENERATED_BODY()

public:
    static constexpr int32 CurrentSchemaVersion =
        FOpenMassCrowdCentralNetworkCustomVersion::LatestVersion;

    virtual void Serialize(FArchive& Ar) override;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Open Mass Crowd|Central")
    int32 SchemaVersion = CurrentSchemaVersion;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Open Mass Crowd|Central")
    FName NetworkId = NAME_None;

    /** Deterministic build identifier supplied by the converter, never generated on load. */
    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Open Mass Crowd|Central")
    FGuid BuildId;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Open Mass Crowd|Central")
    FString GeneratorVersion;

    /** Identifies an asset derived by the reviewed Ground-Only policy. */
    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Open Mass Crowd|Central|Ground Only")
    bool bGroundOnlyNetwork = false;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Open Mass Crowd|Central|Ground Only")
    FString ParentCertifiedSha256;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Open Mass Crowd|Central|Ground Only")
    FString GroundOnlyPolicySha256;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Open Mass Crowd|Central|Ground Only")
    int32 GroundOnlyExcludedSourceFeatureCount = 0;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Open Mass Crowd|Central")
    FBox WorldBounds = FBox(EForceInit::ForceInit);

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Open Mass Crowd|Central")
    FOpenMassCrowdCentralNetworkHashes Hashes;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Open Mass Crowd|Central")
    TArray<FOpenMassCrowdCentralCell> Cells;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Open Mass Crowd|Central")
    TArray<FOpenMassCrowdCentralComponent> Components;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Open Mass Crowd|Central")
    TArray<FOpenMassCrowdCentralSpawnDistrict> SpawnDistricts;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Open Mass Crowd|Central")
    FOpenMassCrowdCentralCertificationEvidence Evidence;

    UFUNCTION(BlueprintPure, Category = "Open Mass Crowd|Central")
    bool IsSchemaVersionSupported() const;

    UFUNCTION(BlueprintPure, Category = "Open Mass Crowd|Central")
    bool HasCompleteCompatibilityHashes() const;

    UFUNCTION(BlueprintPure, Category = "Open Mass Crowd|Central")
    int32 GetCertifiedDirectionalLaneCount() const;

    UFUNCTION(BlueprintPure, Category = "Open Mass Crowd|Central")
    double GetCertifiedDirectionalLaneLengthMeters() const;

    UFUNCTION(BlueprintPure, Category = "Open Mass Crowd|Central")
    int32 GetConfiguredPopulation() const;
};
