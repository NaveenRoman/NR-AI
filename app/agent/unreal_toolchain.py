import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.agent.code_writer import CodeWriter


class UnrealEnvironmentDetector:
    """
    Detects actual Unreal Engine installations, binaries, compilers,
    and Windows SDKs on the Windows host.
    """

    KNOWN_SEARCH_ROOTS = [
        r"C:\Program Files\Epic Games",
        r"C:\Program Files (x86)\Epic Games",
        r"D:\Epic Games",
        r"E:\Epic Games",
        r"C:\Epic Games",
    ]

    @classmethod
    def detect_environment(cls) -> Dict[str, Any]:
        found_engines: List[Dict[str, Any]] = []

        # Check explicit environment variables
        env_paths = [
            os.environ.get("UNREAL_ENGINE_PATH"),
            os.environ.get("UE_ENGINE_DIR"),
            os.environ.get("UE_ROOT"),
        ]
        for ep in env_paths:
            if ep and os.path.exists(ep):
                cls._check_engine_dir(Path(ep), found_engines)

        # Check standard installation roots
        for root in cls.KNOWN_SEARCH_ROOTS:
            if os.path.exists(root):
                try:
                    for entry in os.listdir(root):
                        sub = Path(root) / entry
                        if sub.is_dir() and ("UE_" in entry or "Unreal" in entry):
                            cls._check_engine_dir(sub, found_engines)
                except Exception:
                    pass

        # Compiler and SDK detection
        vswhere = r"C:\Program Files (x86)\Microsoft Visual Studio\Installer\vswhere.exe"
        has_vswhere = os.path.exists(vswhere)
        vs_path = ""
        if has_vswhere:
            try:
                res = subprocess.run(
                    [vswhere, "-latest", "-property", "installationPath"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                vs_path = res.stdout.strip()
            except Exception:
                pass

        cl_path = shutil.which("cl") or shutil.which("cl.exe")
        msvc_available = bool(cl_path or (vs_path and os.path.exists(vs_path)))

        winsdk_dir = r"C:\Program Files (x86)\Windows Kits\10"
        winsdk_versions = []
        if os.path.exists(winsdk_dir):
            inc = Path(winsdk_dir) / "Include"
            if inc.exists():
                try:
                    winsdk_versions = [v for v in os.listdir(inc) if v.startswith("10.")]
                except Exception:
                    pass

        is_available = len(found_engines) > 0
        primary = found_engines[0] if found_engines else {}

        return {
            "is_available": is_available,
            "status": "AVAILABLE" if is_available else "UNAVAILABLE",
            "engines": found_engines,
            "primary_engine_path": primary.get("engine_path"),
            "editor_executable": primary.get("editor_executable"),
            "ubt_executable": primary.get("ubt_executable"),
            "uat_executable": primary.get("uat_executable"),
            "version": primary.get("version", "None"),
            "msvc_available": msvc_available,
            "vs_path": vs_path or "None",
            "winsdk_available": len(winsdk_versions) > 0,
            "winsdk_version": winsdk_versions[-1] if winsdk_versions else "None",
            "summary": (
                f"Unreal Engine {primary.get('version', '')} at {primary.get('engine_path')}"
                if is_available
                else "UNREAL ENGINE: UNAVAILABLE"
            ),
        }

    @classmethod
    def _check_engine_dir(cls, engine_dir: Path, engines_list: List[Dict[str, Any]]) -> None:
        editor_candidates = [
            engine_dir / "Engine" / "Binaries" / "Win64" / "UnrealEditor.exe",
            engine_dir / "Engine" / "Binaries" / "Win64" / "UE4Editor.exe",
        ]
        editor_exe = next((str(c) for c in editor_candidates if c.exists()), None)

        ubt_candidates = [
            engine_dir / "Engine" / "Binaries" / "DotNET" / "UnrealBuildTool" / "UnrealBuildTool.exe",
            engine_dir / "Engine" / "Binaries" / "DotNET" / "UnrealBuildTool.exe",
            engine_dir / "Engine" / "Build" / "BatchFiles" / "Build.bat",
        ]
        ubt_exe = next((str(c) for c in ubt_candidates if c.exists()), None)

        uat_bat = engine_dir / "Engine" / "Build" / "BatchFiles" / "RunUAT.bat"
        uat_exe = str(uat_bat) if uat_bat.exists() else None

        # Determine version from folder name or Version.h
        version = engine_dir.name.replace("UE_", "")

        if editor_exe or ubt_exe or (engine_dir / "Engine").exists():
            engines_list.append({
                "engine_path": str(engine_dir),
                "version": version,
                "editor_executable": editor_exe,
                "ubt_executable": ubt_exe,
                "uat_executable": uat_exe,
                "is_ue5": "5." in version or "UE_5" in str(engine_dir),
            })


class UnrealProjectDetector:
    """
    Detects whether a given directory is an Unreal Engine project (*.uproject)
    and extracts metadata, engine association, modules, and project type.
    """

    @staticmethod
    def detect(project_dir: str | Path) -> Dict[str, Any]:
        p = Path(project_dir).resolve()
        if not p.exists() or not p.is_dir():
            return {"is_unreal": False, "reason": "Directory does not exist"}

        uproject_files = list(p.glob("*.uproject"))
        is_unreal = len(uproject_files) > 0

        if not is_unreal:
            # Check for standard subfolders
            has_source = (p / "Source").exists() and (p / "Source").is_dir()
            has_config = (p / "Config").exists()
            has_content = (p / "Content").exists()
            if has_source and has_config and has_content:
                is_unreal = True

        project_name = uproject_files[0].stem if uproject_files else p.name
        engine_association = "5.3"
        modules: List[str] = []
        plugins: List[str] = []

        if uproject_files:
            try:
                data = json.loads(uproject_files[0].read_text(encoding="utf-8", errors="ignore"))
                engine_association = str(data.get("EngineAssociation", "5.3"))
                modules = [m.get("Name") for m in data.get("Modules", []) if "Name" in m]
                plugins = [pl.get("Name") for pl in data.get("Plugins", []) if "Name" in pl]
            except Exception:
                pass

        has_source = (p / "Source").exists()
        has_content = (p / "Content").exists()
        has_config = (p / "Config").exists()
        is_cpp = has_source and bool(list((p / "Source").glob("**/*.Build.cs")))

        return {
            "is_unreal": is_unreal,
            "project_path": str(p),
            "project_name": project_name,
            "uproject_file": str(uproject_files[0]) if uproject_files else None,
            "engine_association": engine_association,
            "is_cpp": is_cpp,
            "is_blueprint": not is_cpp,
            "modules": modules,
            "plugins": plugins,
            "has_source": has_source,
            "has_content": has_content,
            "has_config": has_config,
        }


class UnrealProjectInspector:
    """
    Inspects Unreal project C++ classes, UCLASS macros, Build.cs modules,
    and Blueprint assets.
    """

    def __init__(self, project_dir: str | Path):
        self.project_dir = Path(project_dir).resolve()

    def inspect(self) -> Dict[str, Any]:
        detection = UnrealProjectDetector.detect(self.project_dir)
        if not detection.get("is_unreal"):
            return {"error": "Not a valid Unreal project", "path": str(self.project_dir)}

        p = self.project_dir
        cpp_files = [str(f.relative_to(p)) for f in (p / "Source").glob("**/*.cpp")] if (p / "Source").exists() else []
        header_files = [str(f.relative_to(p)) for f in (p / "Source").glob("**/*.h")] if (p / "Source").exists() else []
        build_cs_files = [str(f.relative_to(p)) for f in (p / "Source").glob("**/*.Build.cs")] if (p / "Source").exists() else []
        target_cs_files = [str(f.relative_to(p)) for f in (p / "Source").glob("**/*.Target.cs")] if (p / "Source").exists() else []

        classes_found = []
        macros_found = set()

        for hf in (p / "Source").glob("**/*.h") if (p / "Source").exists() else []:
            try:
                txt = hf.read_text(encoding="utf-8", errors="ignore")
                for m in re.finditer(r"class\s+([A-Za-z0-9_]+_API\s+)?([A-Za-z0-9_]+)\s*:\s*public\s+([A-Za-z0-9_]+)", txt):
                    classes_found.append({
                        "name": m.group(2),
                        "parent": m.group(3),
                        "header": str(hf.relative_to(p)),
                    })
                for macro in ["UCLASS", "USTRUCT", "UENUM", "UFUNCTION", "UPROPERTY", "GENERATED_BODY"]:
                    if macro in txt:
                        macros_found.add(macro)
            except Exception:
                pass

        return {
            "project_name": detection.get("project_name"),
            "engine_association": detection.get("engine_association"),
            "is_cpp": detection.get("is_cpp"),
            "cpp_source_files": cpp_files,
            "header_files": header_files,
            "build_cs_files": build_cs_files,
            "target_cs_files": target_cs_files,
            "classes": classes_found,
            "macros_used": sorted(list(macros_found)),
            "blueprint_support_status": "BLUEPRINT MODIFICATION: LIMITED (Binary .uasset format requires Unreal Editor automation)",
        }


class UnrealErrorAnalyzer:
    """
    Analyzes Unreal Engine C++ compilation errors, UHT reflection errors,
    Build.cs module dependencies, and linker issues.
    """

    @classmethod
    def analyze(cls, error_log: str, filepath: Optional[str] = None) -> Dict[str, Any]:
        log = error_log or ""
        log_lower = log.lower()

        # 1. UHT Reflection Error: Missing GENERATED_BODY()
        if "generated_body" in log_lower and ("missing" in log_lower or "expected" in log_lower or "macro" in log_lower):
            line_m = re.search(r":(\d+):", log)
            line = int(line_m.group(1)) if line_m else 1
            return {
                "error_type": "UnrealHeaderToolError",
                "error_category": "reflection",
                "file": filepath or "Header.h",
                "line": line,
                "message": "Class declaration missing GENERATED_BODY() macro required by Unreal Header Tool.",
                "probable_cause": "UCLASS defined without required GENERATED_BODY() reflection macro.",
                "suggested_fix": "Add 'GENERATED_BODY()' immediately after the class opening brace.",
            }

        # 2. UHT Include Order: .generated.h must be last
        if "must be the last include" in log_lower or (".generated.h" in log_lower and "include" in log_lower):
            return {
                "error_type": "UnrealIncludeOrderError",
                "error_category": "header_include",
                "file": filepath or "Header.h",
                "line": 1,
                "message": "#include '*.generated.h' must be the last include in the header file.",
                "probable_cause": "Header includes placed below the generated header.",
                "suggested_fix": "Move '#include \"<Filename>.generated.h\"' to be the very last #include directive.",
            }

        # 3. Undeclared Identifier / C++ Compiler Error (C2065)
        m_c2065 = re.search(r"error C2065:\s*'([^']+)':\s*undeclared identifier", log, re.IGNORECASE)
        if m_c2065:
            ident = m_c2065.group(1)
            line_m = re.search(r"\((\d+)(?:,\d+)?\):", log)
            line = int(line_m.group(1)) if line_m else 1
            return {
                "error_type": "UnrealCppCompilerError",
                "error_category": "syntax",
                "file": filepath or "Source.cpp",
                "line": line,
                "message": f"Undeclared identifier: '{ident}'",
                "probable_cause": f"The variable or function '{ident}' is not declared or missing an #include.",
                "suggested_fix": f"Declare '{ident}' or add the required module header (e.g. #include for component/class).",
            }

        # 4. Syntax Error Missing Semicolon (C2143)
        if "error c2143" in log_lower or "missing ';'" in log_lower:
            line_m = re.search(r"\((\d+)(?:,\d+)?\):", log)
            line = int(line_m.group(1)) if line_m else 1
            return {
                "error_type": "UnrealCppSyntaxError",
                "error_category": "syntax",
                "file": filepath or "Source.cpp",
                "line": line,
                "message": "Missing semicolon ';' in C++ statement.",
                "probable_cause": "Omitted semicolon at end of member declaration or statement.",
                "suggested_fix": "Append missing ';' to the statement.",
            }

        # 5. Linker Error: Unresolved External Symbol (LNK2019 / LNK2001)
        if "lnk2019" in log_lower or "lnk2001" in log_lower or "unresolved external symbol" in log_lower:
            sym_m = re.search(r"unresolved external symbol \"([^\"]+)\"", log)
            sym = sym_m.group(1) if sym_m else "symbol"
            return {
                "error_type": "UnrealLinkerError",
                "error_category": "linker",
                "file": filepath or "Build.cs",
                "line": None,
                "message": f"Unresolved external symbol: {sym}",
                "probable_cause": "Missing module dependency in PublicDependencyModuleNames or missing function definition.",
                "suggested_fix": "Add the required module (e.g. 'UMG', 'EnhancedInput', 'GameplayTasks') to PublicDependencyModuleNames in .Build.cs.",
            }

        # 6. Generic Unreal Build Error Fallback
        return {
            "error_type": "UnrealBuildError",
            "error_category": "build",
            "file": filepath or "UnrealProject",
            "line": None,
            "message": log.strip()[:200] if log else "Unreal Engine build command failed.",
            "probable_cause": "Compiler or UnrealBuildTool failure.",
            "suggested_fix": "Inspect UHT reflection macros, include dependencies, and Build.cs module definitions.",
        }


class UnrealToolchain:
    """
    NR AI Unreal Engine Toolchain Adapter.
    Handles project scaffolding, C++ character & health system generation,
    UnrealBuildTool compilation, editor launching, and self-healing.
    """

    def __init__(self, workspace: Optional[str] = None):
        self.workspace = Path(workspace or os.getcwd()).resolve()
        self.writer = CodeWriter(workspace=str(self.workspace))
        self.env = UnrealEnvironmentDetector.detect_environment()

    def get_toolchain_status(self) -> Dict[str, Any]:
        """Returns the real environment status without faking success."""
        return self.env

    def scaffold_project(
        self,
        project_name: str = "UnrealGame",
        target_dir: Optional[str] = None,
        engine_version: str = "5.3",
        template: str = "third_person",
    ) -> Dict[str, Any]:
        """
        Scaffolds a complete Unreal Engine C++ project structure:
        - .uproject
        - Source/<ProjectName>/<ProjectName>.Build.cs
        - Source/<ProjectName>Target.cs & Source/<ProjectName>EditorTarget.cs
        - Source/<ProjectName>/<ProjectName>.h & .cpp
        - Source/<ProjectName>/GameMode/<ProjectName>GameMode.h & .cpp
        - Source/<ProjectName>/Character/<ProjectName>Character.h & .cpp
        - Source/<ProjectName>/Components/HealthComponent.h & .cpp
        - Config/DefaultEngine.ini & Config/DefaultGame.ini
        """
        base_dir = self.workspace / (target_dir or f"unreal_{project_name.lower()}")
        source_dir = base_dir / "Source" / project_name
        config_dir = base_dir / "Config"
        content_dir = base_dir / "Content"

        for d in [base_dir, source_dir / "GameMode", source_dir / "Character", source_dir / "Components", config_dir, content_dir]:
            d.mkdir(parents=True, exist_ok=True)

        api_macro = f"{project_name.upper()}_API"

        # 1. .uproject JSON
        uproject_content = {
            "FileVersion": 3,
            "EngineAssociation": engine_version,
            "Category": "Games",
            "Description": f"Autonomous Unreal Engine {engine_version} project built by NR-AI",
            "Modules": [
                {
                    "Name": project_name,
                    "Type": "Runtime",
                    "LoadingPhase": "Default",
                    "AdditionalDependencies": ["Engine", "CoreUObject"],
                }
            ],
            "Plugins": [
                {
                    "Name": "EnhancedInput",
                    "Enabled": True,
                }
            ],
        }
        self.writer.write_file(str(base_dir / f"{project_name}.uproject"), json.dumps(uproject_content, indent=2))

        # 2. Source/<ProjectName>.Target.cs
        target_cs = (
            "using UnrealBuildTool;\n"
            "using System.Collections.Generic;\n\n"
            f"public class {project_name}Target : TargetRules\n"
            "{\n"
            f"    public {project_name}Target(TargetInfo Target) : base(Target)\n"
            "    {\n"
            "        Type = TargetType.Game;\n"
            "        DefaultBuildSettings = BuildSettingsVersion.V4;\n"
            "        IncludeOrderVersion = EngineIncludeOrderVersion.Unreal5_3;\n"
            f"        ExtraModuleNames.Add(\"{project_name}\");\n"
            "    }\n"
            "}\n"
        )
        self.writer.write_file(str(base_dir / "Source" / f"{project_name}Target.cs"), target_cs)

        # 3. Source/<ProjectName>EditorTarget.cs
        editor_target_cs = (
            "using UnrealBuildTool;\n"
            "using System.Collections.Generic;\n\n"
            f"public class {project_name}EditorTarget : TargetRules\n"
            "{\n"
            f"    public {project_name}EditorTarget(TargetInfo Target) : base(Target)\n"
            "    {\n"
            "        Type = TargetType.Editor;\n"
            "        DefaultBuildSettings = BuildSettingsVersion.V4;\n"
            "        IncludeOrderVersion = EngineIncludeOrderVersion.Unreal5_3;\n"
            f"        ExtraModuleNames.Add(\"{project_name}\");\n"
            "    }\n"
            "}\n"
        )
        self.writer.write_file(str(base_dir / "Source" / f"{project_name}EditorTarget.cs"), editor_target_cs)

        # 4. Source/<ProjectName>/<ProjectName>.Build.cs
        build_cs = (
            "using UnrealBuildTool;\n\n"
            f"public class {project_name} : ModuleRules\n"
            "{\n"
            f"    public {project_name}(ReadOnlyTargetRules Target) : base(Target)\n"
            "    {\n"
            "        PCHUsage = PCHUsageMode.UseExplicitOrSharedPCHs;\n\n"
            "        PublicDependencyModuleNames.AddRange(new string[] {\n"
            "            \"Core\", \"CoreUObject\", \"Engine\", \"InputCore\", \"EnhancedInput\"\n"
            "        });\n\n"
            "        PrivateDependencyModuleNames.AddRange(new string[] { });\n"
            "    }\n"
            "}\n"
        )
        self.writer.write_file(str(source_dir / f"{project_name}.Build.cs"), build_cs)

        # 5. Source/<ProjectName>/<ProjectName>.h & .cpp
        main_h = (
            "#pragma once\n\n"
            "#include \"CoreMinimal.h\"\n"
        )
        self.writer.write_file(str(source_dir / f"{project_name}.h"), main_h)

        main_cpp = (
            f"#include \"{project_name}.h\"\n"
            "#include \"Modules/ModuleManager.h\"\n\n"
            f"IMPLEMENT_PRIMARY_GAME_MODULE(FDefaultGameModuleImpl, {project_name}, \"{project_name}\");\n"
        )
        self.writer.write_file(str(source_dir / f"{project_name}.cpp"), main_cpp)

        # 6. GameMode
        gm_h = (
            "#pragma once\n\n"
            "#include \"CoreMinimal.h\"\n"
            "#include \"GameFramework/GameModeBase.h\"\n"
            f"#include \"{project_name}GameMode.generated.h\"\n\n"
            f"UCLASS(minimalapi)\n"
            f"class {api_macro} A{project_name}GameMode : public AGameModeBase\n"
            "{\n"
            "    GENERATED_BODY()\n\n"
            "public:\n"
            f"    A{project_name}GameMode();\n"
            "};\n"
        )
        self.writer.write_file(str(source_dir / "GameMode" / f"{project_name}GameMode.h"), gm_h)

        gm_cpp = (
            f"#include \"{project_name}GameMode.h\"\n"
            f"#include \"{project_name}/Character/{project_name}Character.h\"\n\n"
            f"A{project_name}GameMode::A{project_name}GameMode()\n"
            "{\n"
            f"    DefaultPawnClass = A{project_name}Character::StaticClass();\n"
            "}\n"
        )
        self.writer.write_file(str(source_dir / "GameMode" / f"{project_name}GameMode.cpp"), gm_cpp)

        # 7. Character Class
        char_h = (
            "#pragma once\n\n"
            "#include \"CoreMinimal.h\"\n"
            "#include \"GameFramework/Character.h\"\n"
            f"#include \"{project_name}Character.generated.h\"\n\n"
            "class UHealthComponent;\n\n"
            f"UCLASS()\n"
            f"class {api_macro} A{project_name}Character : public ACharacter\n"
            "{\n"
            "    GENERATED_BODY()\n\n"
            "public:\n"
            f"    A{project_name}Character();\n\n"
            "    virtual void BeginPlay() override;\n"
            "    virtual void SetupPlayerInputComponent(class UInputComponent* PlayerInputComponent) override;\n\n"
            "    UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = \"Components\")\n"
            "    UHealthComponent* HealthComponent;\n"
            "};\n"
        )
        self.writer.write_file(str(source_dir / "Character" / f"{project_name}Character.h"), char_h)

        char_cpp = (
            f"#include \"{project_name}Character.h\"\n"
            f"#include \"{project_name}/Components/HealthComponent.h\"\n\n"
            f"A{project_name}Character::A{project_name}Character()\n"
            "{\n"
            "    PrimaryActorTick.bCanEverTick = true;\n"
            "    HealthComponent = CreateDefaultSubobject<UHealthComponent>(TEXT(\"HealthComponent\"));\n"
            "}\n\n"
            f"void A{project_name}Character::BeginPlay()\n"
            "{\n"
            "    Super::BeginPlay();\n"
            "}\n\n"
            f"void A{project_name}Character::SetupPlayerInputComponent(UInputComponent* PlayerInputComponent)\n"
            "{\n"
            "    Super::SetupPlayerInputComponent(PlayerInputComponent);\n"
            "}\n"
        )
        self.writer.write_file(str(source_dir / "Character" / f"{project_name}Character.cpp"), char_cpp)

        # 8. HealthComponent
        health_h = (
            "#pragma once\n\n"
            "#include \"CoreMinimal.h\"\n"
            "#include \"Components/ActorComponent.h\"\n"
            "#include \"HealthComponent.generated.h\"\n\n"
            f"UCLASS(ClassGroup=(Custom), meta=(BlueprintSpawnableComponent))\n"
            f"class {api_macro} UHealthComponent : public UActorComponent\n"
            "{\n"
            "    GENERATED_BODY()\n\n"
            "public:\n"
            "    UHealthComponent();\n\n"
            "    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = \"Health\")\n"
            "    float MaxHealth;\n\n"
            "    UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = \"Health\")\n"
            "    float CurrentHealth;\n\n"
            "    UFUNCTION(BlueprintCallable, Category = \"Health\")\n"
            "    void TakeDamage(float DamageAmount);\n\n"
            "    UFUNCTION(BlueprintCallable, Category = \"Health\")\n"
            "    void Heal(float HealAmount);\n"
            "};\n"
        )
        self.writer.write_file(str(source_dir / "Components" / "HealthComponent.h"), health_h)

        health_cpp = (
            "#include \"HealthComponent.h\"\n\n"
            "UHealthComponent::UHealthComponent()\n"
            "{\n"
            "    PrimaryComponentTick.bCanEverTick = false;\n"
            "    MaxHealth = 100.0f;\n"
            "    CurrentHealth = MaxHealth;\n"
            "}\n\n"
            "void UHealthComponent::TakeDamage(float DamageAmount)\n"
            "{\n"
            "    CurrentHealth = FMath::Clamp(CurrentHealth - DamageAmount, 0.0f, MaxHealth);\n"
            "}\n\n"
            "void UHealthComponent::Heal(float HealAmount)\n"
            "{\n"
            "    CurrentHealth = FMath::Clamp(CurrentHealth + HealAmount, 0.0f, MaxHealth);\n"
            "}\n"
        )
        self.writer.write_file(str(source_dir / "Components" / "HealthComponent.cpp"), health_cpp)

        # 9. Config Files
        engine_ini = (
            "[/Script/EngineSettings.GameMapsSettings]\n"
            f"GlobalDefaultGameMode=/Script/{project_name}.{project_name}GameMode\n"
        )
        self.writer.write_file(str(config_dir / "DefaultEngine.ini"), engine_ini)

        game_ini = (
            "[/Script/EngineSettings.GeneralProjectSettings]\n"
            f"ProjectID=A1B2C3D4E5F67890\n"
            f"ProjectName={project_name}\n"
        )
        self.writer.write_file(str(config_dir / "DefaultGame.ini"), game_ini)

        return {
            "success": True,
            "project_name": project_name,
            "project_path": str(base_dir),
            "uproject_file": str(base_dir / f"{project_name}.uproject"),
            "engine_version": engine_version,
            "classes_generated": [
                f"{project_name}GameMode",
                f"{project_name}Character",
                "HealthComponent",
            ],
            "toolchain_status": self.env.get("status"),
        }

    def modify_health_system(self, project_dir: str | Path, max_health: float = 200.0) -> Dict[str, Any]:
        """Modifies HealthComponent default values and methods for multi-turn testing."""
        p = Path(project_dir).resolve()
        health_cpp = list(p.glob("Source/**/HealthComponent.cpp"))
        if not health_cpp:
            return {"success": False, "error": "HealthComponent.cpp not found"}

        target = health_cpp[0]
        content = target.read_text(encoding="utf-8")
        updated = content.replace("MaxHealth = 100.0f;", f"MaxHealth = {max_health:.1f}f;")
        self.writer.write_file(str(target), updated)
        return {"success": True, "file": str(target), "new_max_health": max_health}

    def build_project(self, project_dir: str | Path) -> Dict[str, Any]:
        """
        Executes UnrealBuildTool if available on the host machine.
        Reports UNAVAILABLE if toolchain is missing.
        """
        det = UnrealProjectDetector.detect(project_dir)
        if not det.get("is_unreal"):
            return {"success": False, "error": "Not a valid Unreal project"}

        ubt = self.env.get("ubt_executable")
        if not ubt or not os.path.exists(ubt):
            return {
                "success": False,
                "status": "UNAVAILABLE",
                "message": "UnrealBuildTool executable is not available on this host.",
                "project": det.get("project_name"),
            }

        # Real UBT execution when tool is present
        cmd = [ubt, f"{det['project_name']}Editor", "Win64", "Development", det["uproject_file"], "-waitmutex"]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            return {
                "success": res.returncode == 0,
                "exit_code": res.returncode,
                "stdout": res.stdout,
                "stderr": res.stderr,
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    def launch_editor(self, project_dir: str | Path) -> Dict[str, Any]:
        """Launches UnrealEditor.exe with project path if installed."""
        editor = self.env.get("editor_executable")
        if not editor or not os.path.exists(editor):
            return {
                "success": False,
                "status": "UNAVAILABLE",
                "message": "UnrealEditor.exe is not installed on this host.",
            }

        det = UnrealProjectDetector.detect(project_dir)
        try:
            proc = subprocess.Popen([editor, det["uproject_file"]])
            return {"success": True, "pid": proc.pid, "message": "Editor launched"}
        except Exception as e:
            return {"success": False, "error": str(e)}
