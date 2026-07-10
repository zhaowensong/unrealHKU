#pragma once

#include "CoreMinimal.h"
#include "Kismet/BlueprintFunctionLibrary.h"
#include "SignalSimulationTypes.h"
#include "SignalSimulationDataLibrary.generated.h"

UCLASS()
class TELECOMTWIN_API USignalSimulationDataLibrary : public UBlueprintFunctionLibrary
{
    GENERATED_BODY()

public:
    UFUNCTION(BlueprintCallable, Category = "Signal Simulation")
    static bool LoadSimulationFrameFromJson(
        const FString& FilePath,
        FSignalSimulationFrame& OutFrame,
        FString& OutError);
};
