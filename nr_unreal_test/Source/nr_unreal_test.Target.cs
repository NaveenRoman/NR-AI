using UnrealBuildTool;
using System.Collections.Generic;

public class nr_unreal_testTarget : TargetRules
{
	public nr_unreal_testTarget(TargetInfo Target) : base(Target)
	{
		Type = TargetType.Game;
		DefaultBuildSettings = BuildSettingsVersion.V5;
		IncludeOrderVersion = EngineIncludeOrderVersion.Latest;
		ExtraModuleNames.Add("nr_unreal_test");
	}
}
