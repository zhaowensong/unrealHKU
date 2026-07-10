#pragma once
#include "CoreMinimal.h"
#include "Engine/DeveloperSettings.h"
#include "MCPConstants.h"
#include "MCPSettings.generated.h"

UCLASS(config = Editor, defaultconfig)
class UNREALMCP_API UMCPSettings : public UDeveloperSettings
{
    GENERATED_BODY()
public:
    UPROPERTY(config, EditAnywhere, Category = "MCP", meta = (ClampMin = "1024", ClampMax = "65535"))
    int32 Port = MCPConstants::DEFAULT_PORT;

    /** Optional fixed token. Leave blank to use TELECOMTWIN_MCP_TOKEN or Saved/MCP/auth-token.txt. */
    UPROPERTY(config, EditAnywhere, Category = "MCP|Security")
    FString AuthenticationToken;

    /** Inline Python is always disabled. This controls approved file execution only. */
    UPROPERTY(config, EditAnywhere, Category = "MCP|Security")
    bool bAllowPythonFileExecution = true;

    /** Project-relative root containing Python files that MCP may execute. */
    UPROPERTY(config, EditAnywhere, Category = "MCP|Security")
    FString AllowedPythonSubdirectory = TEXT("Scripts");

    /** Start the loopback-only authenticated server after editor initialization. */
    UPROPERTY(config, EditAnywhere, Category = "MCP")
    bool bStartServerAutomatically = true;
};
