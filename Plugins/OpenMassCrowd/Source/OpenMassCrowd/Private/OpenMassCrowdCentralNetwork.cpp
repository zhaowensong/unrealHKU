#include "OpenMassCrowdCentralNetwork.h"

#include "Serialization/CustomVersion.h"

const FGuid FOpenMassCrowdCentralNetworkCustomVersion::GUID(
    0xEEC24696,
    0xDB584E19,
    0xB5BE5AF1,
    0xC576C0CF);

namespace
{
FCustomVersionRegistration GRegisterOpenMassCrowdCentralNetworkCustomVersion(
    FOpenMassCrowdCentralNetworkCustomVersion::GUID,
    FOpenMassCrowdCentralNetworkCustomVersion::LatestVersion,
    TEXT("OpenMassCrowdCentralNetwork"));
}

bool FOpenMassCrowdCentralNetworkHashes::HasRequiredCompatibilityInputs() const
{
    return !TopologySha256.IsEmpty()
        && !GeoreferenceSha256.IsEmpty()
        && !TilesetSha256.IsEmpty()
        && !CollisionSettingsSha256.IsEmpty()
        && !CombinedSha256.IsEmpty();
}

void UOpenMassCrowdCentralNetworkDataAsset::Serialize(FArchive& Ar)
{
    Ar.UsingCustomVersion(FOpenMassCrowdCentralNetworkCustomVersion::GUID);
    Super::Serialize(Ar);
}

bool UOpenMassCrowdCentralNetworkDataAsset::IsSchemaVersionSupported() const
{
    if (SchemaVersion != CurrentSchemaVersion)
    {
        return false;
    }

    for (const FOpenMassCrowdCentralCell& Cell : Cells)
    {
        if (Cell.SchemaVersion != CurrentSchemaVersion)
        {
            return false;
        }
    }

    return true;
}

bool UOpenMassCrowdCentralNetworkDataAsset::HasCompleteCompatibilityHashes() const
{
    if (!Hashes.HasRequiredCompatibilityInputs())
    {
        return false;
    }

    for (const FOpenMassCrowdCentralCell& Cell : Cells)
    {
        if (!Cell.Hashes.HasRequiredCompatibilityInputs()
            || Cell.Hashes.CellContentSha256.IsEmpty())
        {
            return false;
        }
    }

    return true;
}

int32 UOpenMassCrowdCentralNetworkDataAsset::GetCertifiedDirectionalLaneCount() const
{
    int32 Count = 0;
    for (const FOpenMassCrowdCentralCell& Cell : Cells)
    {
        if (!Cell.bCertified)
        {
            continue;
        }

        for (const FOpenMassCrowdCentralDirectedLane& Lane : Cell.DirectedLanes)
        {
            Count += Lane.bCertified && Lane.bGroundOnlyEligible ? 1 : 0;
        }
    }
    return Count;
}

double UOpenMassCrowdCentralNetworkDataAsset::GetCertifiedDirectionalLaneLengthMeters() const
{
    double LengthCm = 0.0;
    for (const FOpenMassCrowdCentralCell& Cell : Cells)
    {
        if (!Cell.bCertified)
        {
            continue;
        }

        for (const FOpenMassCrowdCentralDirectedLane& Lane : Cell.DirectedLanes)
        {
            if (Lane.bCertified && Lane.bGroundOnlyEligible)
            {
                LengthCm += Lane.LengthCm;
            }
        }
    }
    return LengthCm / 100.0;
}

int32 UOpenMassCrowdCentralNetworkDataAsset::GetConfiguredPopulation() const
{
    int32 Population = 0;
    for (const FOpenMassCrowdCentralSpawnDistrict& District : SpawnDistricts)
    {
        if (District.bEnabled)
        {
            Population += FMath::Max(District.TargetPopulation, 0);
        }
    }
    return Population;
}
