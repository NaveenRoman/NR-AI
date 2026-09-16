using UnrealBuildTool;
using System.Collections.Generic;

public class nr_unreal_testEditorTarget : TargetRules
{
	public nr_unreal_testEditorTarget(TargetInfo Target) : base(Target)
	{
		Type = TargetType.Editor;
		DefaultBuildSettings = BuildSettingsVersion.V5;
		IncludeOrderVersion = EngineIncludeOrderVersion.Latest;
		ExtraModuleNames.Add("nr_unreal_test");
	}
}
