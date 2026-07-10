#include "SignalSimulationDataLibrary.h"

#include "JsonObjectConverter.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"

bool USignalSimulationDataLibrary::LoadSimulationFrameFromJson(
    const FString& FilePath,
    FSignalSimulationFrame& OutFrame,
    FString& OutError)
{
    const FString ResolvedPath = FPaths::ConvertRelativePathToFull(FilePath);
    FString JsonText;
    if (!FFileHelper::LoadFileToString(JsonText, *ResolvedPath))
    {
        OutError = FString::Printf(TEXT("Could not read signal simulation frame: %s"), *ResolvedPath);
        return false;
    }

    if (!FJsonObjectConverter::JsonObjectStringToUStruct(JsonText, &OutFrame, 0, 0))
    {
        OutError = FString::Printf(TEXT("Invalid signal simulation JSON: %s"), *ResolvedPath);
        return false;
    }

    if (OutFrame.SchemaVersion != TEXT("telecom-twin.signal-frame/1.0"))
    {
        OutError = FString::Printf(TEXT("Unsupported schema_version: %s"), *OutFrame.SchemaVersion);
        return false;
    }

    if (OutFrame.Transmitters.IsEmpty())
    {
        OutError = TEXT("Signal simulation frame contains no transmitters.");
        return false;
    }

    OutError.Reset();
    return true;
}
