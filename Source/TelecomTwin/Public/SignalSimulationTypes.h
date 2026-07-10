#pragma once

#include "CoreMinimal.h"
#include "UObject/Interface.h"
#include "SignalSimulationTypes.generated.h"

USTRUCT(BlueprintType)
struct TELECOMTWIN_API FSignalTransmitterRecord
{
    GENERATED_BODY()

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Signal Simulation")
    FName Id = NAME_None;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Signal Simulation")
    FVector Position = FVector::ZeroVector;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Signal Simulation", meta = (ClampMin = "1.0"))
    float FrequencyMHz = 3500.0f;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Signal Simulation")
    float TransmitPowerDbm = 46.0f;
};

USTRUCT(BlueprintType)
struct TELECOMTWIN_API FSignalSimulationSegment
{
    GENERATED_BODY()

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Signal Simulation")
    FVector Start = FVector::ZeroVector;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Signal Simulation")
    FVector End = FVector::ZeroVector;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Signal Simulation")
    float ReceivedPowerDbm = -120.0f;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Signal Simulation", meta = (ClampMin = "0.0", ClampMax = "1.0"))
    float NormalizedStrength = 0.0f;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Signal Simulation", meta = (ClampMin = "0"))
    int32 BounceIndex = 0;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Signal Simulation")
    FName SourceId = NAME_None;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Signal Simulation")
    bool bReflectionHit = false;
};

USTRUCT(BlueprintType)
struct TELECOMTWIN_API FSignalSimulationFrame
{
    GENERATED_BODY()

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Signal Simulation")
    FString SchemaVersion = TEXT("telecom-twin.signal-frame/1.0");

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Signal Simulation")
    FString FrameId;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Signal Simulation")
    TArray<FSignalTransmitterRecord> Transmitters;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Signal Simulation")
    TArray<FSignalSimulationSegment> Segments;
};

UINTERFACE(BlueprintType)
class TELECOMTWIN_API USignalSimulationProvider : public UInterface
{
    GENERATED_BODY()
};

class TELECOMTWIN_API ISignalSimulationProvider
{
    GENERATED_BODY()

public:
    UFUNCTION(BlueprintNativeEvent, BlueprintCallable, Category = "Signal Simulation")
    bool GetLatestSignalSimulationFrame(FSignalSimulationFrame& OutFrame, FString& OutError);
};
