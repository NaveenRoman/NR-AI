import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.agent.code_writer import CodeWriter


class AndroidProjectDetector:
    """
    Detects whether a directory contains an Android project and identifies
    its build system (Gradle/KTS), source language (Kotlin/Java), and UI framework (Compose/XML).
    """

    @staticmethod
    def detect(project_dir: str | Path) -> Dict[str, Any]:
        p = Path(project_dir).resolve()
        if not p.exists() or not p.is_dir():
            return {"is_android": False, "reason": "Directory does not exist"}

        has_settings_gradle = (p / "settings.gradle").exists() or (p / "settings.gradle.kts").exists()
        has_build_gradle = (p / "build.gradle").exists() or (p / "build.gradle.kts").exists()
        has_app_module = (p / "app").exists() and (
            (p / "app" / "build.gradle").exists() or (p / "app" / "build.gradle.kts").exists()
        )
        has_manifest = (p / "app" / "src" / "main" / "AndroidManifest.xml").exists() or (
            p / "src" / "main" / "AndroidManifest.xml"
        ).exists()

        is_android = (has_settings_gradle and has_build_gradle) or has_manifest or has_app_module

        if not is_android:
            return {"is_android": False, "reason": "No Gradle or AndroidManifest markers found."}

        # Detect build language
        is_kts = (p / "build.gradle.kts").exists() or (p / "app" / "build.gradle.kts").exists()
        build_language = "kotlin_dsl" if is_kts else "groovy_dsl"

        # Detect source languages
        has_kt = bool(list(p.glob("**/*.kt")))
        has_java = bool(list(p.glob("**/*.java")))
        if has_kt and has_java:
            lang = "kotlin_and_java"
        elif has_kt:
            lang = "kotlin"
        elif has_java:
            lang = "java"
        else:
            lang = "unknown"

        # Detect Compose vs XML Layouts
        has_compose = False
        app_build = (p / "app" / "build.gradle.kts") if is_kts else (p / "app" / "build.gradle")
        if app_build.exists():
            content = app_build.read_text(encoding="utf-8", errors="ignore")
            if "compose" in content.lower() or "androidx.compose" in content:
                has_compose = True

        has_xml_layouts = bool(list(p.glob("**/res/layout/*.xml")))
        if has_compose and has_xml_layouts:
            ui_framework = "compose_and_xml"
        elif has_compose:
            ui_framework = "jetpack_compose"
        elif has_xml_layouts:
            ui_framework = "xml_layouts"
        else:
            ui_framework = "jetpack_compose" if has_kt else "xml_layouts"

        return {
            "is_android": True,
            "project_path": str(p),
            "build_language": build_language,
            "source_language": lang,
            "ui_framework": ui_framework,
            "has_app_module": has_app_module,
            "has_manifest": has_manifest,
        }


class AndroidProjectInspector:
    """
    Deeply inspects an Android project: modules, dependencies, SDK versions,
    source files, activities, and resources.
    """

    def __init__(self, project_dir: str | Path):
        self.project_dir = Path(project_dir).resolve()

    def inspect(self) -> Dict[str, Any]:
        detection = AndroidProjectDetector.detect(self.project_dir)
        if not detection.get("is_android"):
            return detection

        modules = self._find_modules()
        app_info = self._inspect_app_module()
        source_files = self._list_source_files()
        resources = self._inspect_resources()

        return {
            "success": True,
            "is_android": True,
            "project_path": str(self.project_dir),
            "build_language": detection["build_language"],
            "source_language": detection["source_language"],
            "ui_framework": detection["ui_framework"],
            "modules": modules,
            "app_module": app_info,
            "source_files_count": len(source_files),
            "source_files": source_files[:20],
            "resources": resources,
        }

    def _find_modules(self) -> List[str]:
        modules = []
        for d in self.project_dir.iterdir():
            if d.is_dir() and ((d / "build.gradle").exists() or (d / "build.gradle.kts").exists()):
                modules.append(d.name)
        return modules or ["app"]

    def _inspect_app_module(self) -> Dict[str, Any]:
        app_dir = self.project_dir / "app"
        build_file = app_dir / "build.gradle.kts"
        if not build_file.exists():
            build_file = app_dir / "build.gradle"

        compile_sdk = None
        target_sdk = None
        min_sdk = None
        namespace = None
        dependencies: List[str] = []

        if build_file.exists():
            text = build_file.read_text(encoding="utf-8", errors="ignore")
            ns_match = re.search(r'namespace\s*=\s*["\']([^"\']+)["\']', text)
            if ns_match:
                namespace = ns_match.group(1)

            compile_match = re.search(r'compileSdk\s*=?\s*(\d+)', text)
            if compile_match:
                compile_sdk = int(compile_match.group(1))

            min_match = re.search(r'minSdk\s*=?\s*(\d+)', text)
            if min_match:
                min_sdk = int(min_match.group(1))

            target_match = re.search(r'targetSdk\s*=?\s*(\d+)', text)
            if target_match:
                target_sdk = int(target_match.group(1))

            # Extract implementation dependencies
            dep_matches = re.findall(r'implementation\s*\(?["\']([^"\']+)["\']\)?', text)
            dependencies.extend(dep_matches)

        return {
            "namespace": namespace or "com.example.nrai",
            "compile_sdk": compile_sdk or 34,
            "min_sdk": min_sdk or 24,
            "target_sdk": target_sdk or 34,
            "dependencies_count": len(dependencies),
            "dependencies": dependencies,
        }

    def _list_source_files(self) -> List[str]:
        sources = []
        for ext in ("*.kt", "*.java"):
            for f in self.project_dir.glob(f"**/{ext}"):
                if "build" not in f.parts and ".gradle" not in f.parts:
                    sources.append(str(f.relative_to(self.project_dir)).replace("\\", "/"))
        return sorted(sources)

    def _inspect_resources(self) -> Dict[str, Any]:
        res_dir = self.project_dir / "app" / "src" / "main" / "res"
        if not res_dir.exists():
            return {"layouts": 0, "drawables": 0, "values": 0}

        layouts = list(res_dir.glob("layout/*.xml"))
        drawables = list(res_dir.glob("drawable*/*"))
        values = list(res_dir.glob("values/*.xml"))

        return {
            "layouts": len(layouts),
            "layout_files": [f.name for f in layouts],
            "drawables": len(drawables),
            "values": len(values),
        }


class AndroidErrorAnalyzer:
    """
    Parses and categorizes Android and Gradle build and compilation errors.
    """

    @staticmethod
    def analyze(log: str) -> Dict[str, Any]:
        text = str(log or "")

        # 1. Kotlin Compiler Error
        # e.g., e: C:/.../MainActivity.kt:15:20 Unresolved reference: Firebase
        kt_match = re.search(r"e:\s+([^\r\n]+?\.(?:kt|java)):(\d+):(\d+)\s+([^\r\n]+)", text)
        if kt_match:
            filepath, line_no, col_no, error_desc = kt_match.groups()
            return {
                "category": "kotlin_compiler",
                "file": filepath,
                "line": int(line_no),
                "column": int(col_no),
                "error": error_desc.strip(),
                "diagnosis": f"Kotlin compilation error: {error_desc.strip()}",
                "suggestion": "Check import declarations or missing dependencies in build.gradle.kts.",
            }

        # 2. Java Compiler Error
        # e.g., MainActivity.java:12: error: cannot find symbol
        java_match = re.search(r"([^\r\n]+?\.java):(\d+):\s+error:\s+([^\r\n]+)", text)
        if java_match:
            filepath, line_no, error_desc = java_match.groups()
            return {
                "category": "java_compiler",
                "file": filepath,
                "line": int(line_no),
                "error": error_desc.strip(),
                "diagnosis": f"Java compilation error: {error_desc.strip()}",
                "suggestion": "Verify class name, imports, and syntax.",
            }

        # 3. Gradle Dependency / Plugin Resolution Error
        if "Could not find" in text or "Could not resolve" in text or "Plugin [id:" in text:
            return {
                "category": "gradle_dependency",
                "file": "build.gradle.kts",
                "error": "Dependency or plugin resolution failed.",
                "diagnosis": "Gradle could not download or resolve one of the declared dependencies/plugins.",
                "suggestion": "Check repository declarations (google(), mavenCentral()) and artifact coordinates.",
            }

        # 4. Android Resource / Manifest Error
        if "resource" in text.lower() and ("not found" in text.lower() or "linking failed" in text.lower()):
            return {
                "category": "android_resource",
                "file": "app/src/main/res",
                "error": "AAPT / Resource linking error.",
                "diagnosis": "Referenced resource ID (string, color, drawable, or layout) is missing or malformed.",
                "suggestion": "Check strings.xml, colors.xml, and drawables.",
            }

        # 5. Manifest Merge Conflict
        if "Manifest merger failed" in text:
            return {
                "category": "manifest_merge",
                "file": "AndroidManifest.xml",
                "error": "Manifest merger failed with multiple errors.",
                "diagnosis": "Conflicting attributes or permissions between app manifest and library manifest.",
                "suggestion": "Add tools:replace in AndroidManifest.xml for conflicting attributes.",
            }

        return {
            "category": "unknown_android_error",
            "file": "app",
            "error": text[:300].strip(),
            "diagnosis": "Gradle task execution failed.",
            "suggestion": "Inspect full Gradle stack trace using './gradlew --stacktrace'.",
        }


class AndroidToolchain:
    """
    Android Toolchain Manager for NR AI.

    Manages:
    - Scaffolding new modern Android Jetpack Compose & XML applications
    - Adding screens, views, and activities
    - Injecting dependencies (Firebase, Retrofit, Room, Coroutines)
    - Running Gradle builds and unit tests
    - Probing connected ADB devices
    """

    def __init__(self, workspace: Optional[str] = None):
        self.workspace = Path(workspace or os.getcwd()).resolve()
        self.writer = CodeWriter(workspace=str(self.workspace))

    def scaffold_project(
        self,
        project_name: str = "AndroidApp",
        package_name: str = "com.example.nrai",
        target_dir: Optional[str] = None,
        use_compose: bool = True,
    ) -> Dict[str, Any]:
        """Scaffolds a complete, modern Android application architecture."""
        root = (self.workspace / (target_dir or f"data/{project_name.lower()}")).resolve()
        root.mkdir(parents=True, exist_ok=True)

        package_path = package_name.replace(".", "/")

        # 1. settings.gradle.kts
        settings_kts = (
            'pluginManagement {\n'
            '    repositories {\n'
            '        google()\n'
            '        mavenCentral()\n'
            '        gradlePluginPortal()\n'
            '    }\n'
            '}\n'
            'dependencyResolutionManagement {\n'
            '    repositoriesMode.set(RepositoriesMode.FAIL_ON_PROJECT_REPOS)\n'
            '    repositories {\n'
            '        google()\n'
            '        mavenCentral()\n'
            '    }\n'
            '}\n'
            f'rootProject.name = "{project_name}"\n'
            'include(":app")\n'
        )

        # 2. Root build.gradle.kts
        root_build_kts = (
            'plugins {\n'
            '    alias(libs.plugins.android.application) apply false\n'
            '    alias(libs.plugins.kotlin.android) apply false\n'
            '}\n'
        )

        # 3. app/build.gradle.kts
        app_build_kts = (
            'plugins {\n'
            '    id("com.android.application")\n'
            '    id("org.jetbrains.kotlin.android")\n'
            '}\n\n'
            'android {\n'
            f'    namespace = "{package_name}"\n'
            '    compileSdk = 34\n\n'
            '    defaultConfig {\n'
            f'        applicationId = "{package_name}"\n'
            '        minSdk = 24\n'
            '        targetSdk = 34\n'
            '        versionCode = 1\n'
            '        versionName = "1.0"\n'
            '        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"\n'
            '    }\n\n'
            '    buildFeatures {\n'
            f'        compose = {str(use_compose).lower()}\n'
            '    }\n'
            '    compileOptions {\n'
            '        sourceCompatibility = JavaVersion.VERSION_17\n'
            '        targetCompatibility = JavaVersion.VERSION_17\n'
            '    }\n'
            '    kotlinOptions {\n'
            '        jvmTarget = "17"\n'
            '    }\n'
            '}\n\n'
            'dependencies {\n'
            '    implementation("androidx.core:core-ktx:1.12.0")\n'
            '    implementation("androidx.lifecycle:lifecycle-runtime-ktx:2.7.0")\n'
            '    implementation("androidx.activity:activity-compose:1.8.2")\n'
            '    implementation(platform("androidx.compose:compose-bom:2024.02.00"))\n'
            '    implementation("androidx.compose.ui:ui")\n'
            '    implementation("androidx.compose.ui:ui-graphics")\n'
            '    implementation("androidx.compose.ui:ui-tooling-preview")\n'
            '    implementation("androidx.compose.material3:material3")\n'
            '    testImplementation("junit:junit:4.13.2")\n'
            '    androidTestImplementation("androidx.test.ext:junit:1.1.5")\n'
            '}\n'
        )

        # 4. AndroidManifest.xml
        manifest_xml = (
            '<?xml version="1.0" encoding="utf-8"?>\n'
            '<manifest xmlns:android="http://schemas.android.com/apk/res/android">\n'
            '    <application\n'
            '        android:allowBackup="true"\n'
            '        android:icon="@mipmap/ic_launcher"\n'
            f'        android:label="{project_name}"\n'
            '        android:roundIcon="@mipmap/ic_launcher_round"\n'
            '        android:supportsRtl="true"\n'
            '        android:theme="@style/Theme.NRApp">\n'
            '        <activity\n'
            '            android:name=".MainActivity"\n'
            '            android:exported="true"\n'
            '            android:theme="@style/Theme.NRApp">\n'
            '            <intent-filter>\n'
            '                <action android:name="android.intent.action.MAIN" />\n'
            '                <category android:name="android.intent.category.LAUNCHER" />\n'
            '            </intent-filter>\n'
            '        </activity>\n'
            '    </application>\n'
            '</manifest>\n'
        )

        # 5. MainActivity.kt
        main_activity_kt = (
            f'package {package_name}\n\n'
            'import android.os.Bundle\n'
            'import androidx.activity.ComponentActivity\n'
            'import androidx.activity.compose.setContent\n'
            'import androidx.compose.foundation.layout.*\n'
            'import androidx.compose.material3.*\n'
            'import androidx.compose.runtime.*\n'
            'import androidx.compose.ui.Alignment\n'
            'import androidx.compose.ui.Modifier\n'
            'import androidx.compose.ui.unit.dp\n\n'
            'class MainActivity : ComponentActivity() {\n'
            '    override fun onCreate(savedInstanceState: Bundle?) {\n'
            '        super.onCreate(savedInstanceState)\n'
            '        setContent {\n'
            '            MaterialTheme {\n'
            '                Surface(\n'
            '                    modifier = Modifier.fillMaxSize(),\n'
            '                    color = MaterialTheme.colorScheme.background\n'
            '                ) {\n'
            f'                    MainGreetingScreen(title = "{project_name}")\n'
            '                }\n'
            '            }\n'
            '        }\n'
            '    }\n'
            '}\n\n'
            '@Composable\n'
            'fun MainGreetingScreen(title: String) {\n'
            '    Column(\n'
            '        modifier = Modifier.fillMaxSize().padding(24.dp),\n'
            '        verticalArrangement = Arrangement.Center,\n'
            '        horizontalAlignment = Alignment.CenterHorizontally\n'
            '    ) {\n'
            '        Text(text = "Welcome to $title", style = MaterialTheme.typography.headlineMedium)\n'
            '        Spacer(modifier = Modifier.height(16.dp))\n'
            '        Button(onClick = { /* Action */ }) {\n'
            '            Text(text = "Get Started")\n'
            '        }\n'
            '    }\n'
            '}\n'
        )

        # 6. Color.kt & Theme.kt
        color_kt = (
            f'package {package_name}.ui.theme\n\n'
            'import androidx.compose.ui.graphics.Color\n\n'
            'val Purple80 = Color(0xFFD0BCFF)\n'
            'val PurpleGrey80 = Color(0xFFCCC2DC)\n'
            'val Pink80 = Color(0xFFEFB8C8)\n\n'
            'val PrimaryButtonColor = Color(0xFF6200EE)\n'
            'val SecondaryButtonColor = Color(0xFF03DAC6)\n'
        )

        # 7. strings.xml & themes.xml
        strings_xml = (
            '<?xml version="1.0" encoding="utf-8"?>\n'
            '<resources>\n'
            f'    <string name="app_name">{project_name}</string>\n'
            '</resources>\n'
        )
        themes_xml = (
            '<?xml version="1.0" encoding="utf-8"?>\n'
            '<resources>\n'
            '    <style name="Theme.NRApp" parent="android:Theme.Material.Light.NoActionBar" />\n'
            '</resources>\n'
        )

        # 8. Unit Test: ExampleUnitTest.kt
        unit_test_kt = (
            f'package {package_name}\n\n'
            'import org.junit.Test\n'
            'import org.junit.Assert.*\n\n'
            'class ExampleUnitTest {\n'
            '    @Test\n'
            '    fun addition_isCorrect() {\n'
            '        assertEquals(4, 2 + 2)\n'
            '    }\n'
            '}\n'
        )

        # Write files
        files_to_write = {
            root / "settings.gradle.kts": settings_kts,
            root / "build.gradle.kts": root_build_kts,
            root / "app" / "build.gradle.kts": app_build_kts,
            root / "app" / "src" / "main" / "AndroidManifest.xml": manifest_xml,
            root / "app" / "src" / "main" / "java" / package_path / "MainActivity.kt": main_activity_kt,
            root / "app" / "src" / "main" / "java" / package_path / "ui" / "theme" / "Color.kt": color_kt,
            root / "app" / "src" / "main" / "res" / "values" / "strings.xml": strings_xml,
            root / "app" / "src" / "main" / "res" / "values" / "themes.xml": themes_xml,
            root / "app" / "src" / "test" / "java" / package_path / "ExampleUnitTest.kt": unit_test_kt,
        }

        for path, content in files_to_write.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")

        return {
            "success": True,
            "project_name": project_name,
            "package_name": package_name,
            "project_path": str(root),
            "files_created": len(files_to_write),
        }

    def add_dependency(self, project_dir: str | Path, dependency_coord: str) -> Dict[str, Any]:
        """Injects a Gradle dependency into app/build.gradle.kts or app/build.gradle."""
        p = Path(project_dir).resolve()
        app_build = p / "app" / "build.gradle.kts"
        if not app_build.exists():
            app_build = p / "app" / "build.gradle"

        if not app_build.exists():
            return {"success": False, "message": "Could not find app build.gradle(.kts)"}

        text = app_build.read_text(encoding="utf-8")
        if dependency_coord in text:
            return {"success": True, "message": f"Dependency '{dependency_coord}' already exists."}

        # Insert inside dependencies block
        dep_entry = f'    implementation("{dependency_coord}")\n'
        if "dependencies {" in text:
            updated = text.replace("dependencies {\n", f"dependencies {{\n{dep_entry}", 1)
        else:
            updated = text + f"\ndependencies {{\n{dep_entry}}}\n"

        app_build.write_text(updated, encoding="utf-8")
        return {"success": True, "message": f"Added dependency '{dependency_coord}' to {app_build.name}"}

    def add_login_screen(self, project_dir: str | Path, package_name: str = "com.example.nrai") -> Dict[str, Any]:
        """Adds a complete Jetpack Compose LoginScreen component."""
        p = Path(project_dir).resolve()
        package_path = package_name.replace(".", "/")
        login_file = p / "app" / "src" / "main" / "java" / package_path / "LoginScreen.kt"

        login_code = (
            f'package {package_name}\n\n'
            'import androidx.compose.foundation.layout.*\n'
            'import androidx.compose.material3.*\n'
            'import androidx.compose.runtime.*\n'
            'import androidx.compose.ui.Alignment\n'
            'import androidx.compose.ui.Modifier\n'
            'import androidx.compose.ui.text.input.PasswordVisualTransformation\n'
            'import androidx.compose.ui.unit.dp\n\n'
            '@Composable\n'
            'fun LoginScreen(\n'
            '    onLoginSuccess: (String) -> Unit = {}\n'
            ') {\n'
            '    var email by remember { mutableStateOf("") }\n'
            '    var password by remember { mutableStateOf("") }\n'
            '    var errorMessage by remember { mutableStateOf<String?>(null) }\n\n'
            '    Column(\n'
            '        modifier = Modifier.fillMaxSize().padding(24.dp),\n'
            '        verticalArrangement = Arrangement.Center,\n'
            '        horizontalAlignment = Alignment.CenterHorizontally\n'
            '    ) {\n'
            '        Text(text = "Sign In", style = MaterialTheme.typography.headlineLarge)\n'
            '        Spacer(modifier = Modifier.height(24.dp))\n\n'
            '        OutlinedTextField(\n'
            '            value = email,\n'
            '            onValueChange = { email = it },\n'
            '            label = { Text("Email Address") },\n'
            '            modifier = Modifier.fillMaxWidth()\n'
            '        )\n'
            '        Spacer(modifier = Modifier.height(16.dp))\n\n'
            '        OutlinedTextField(\n'
            '            value = password,\n'
            '            onValueChange = { password = it },\n'
            '            label = { Text("Password") },\n'
            '            visualTransformation = PasswordVisualTransformation(),\n'
            '            modifier = Modifier.fillMaxWidth()\n'
            '        )\n'
            '        Spacer(modifier = Modifier.height(24.dp))\n\n'
            '        Button(\n'
            '            onClick = {\n'
            '                if (email.isNotBlank() && password.length >= 6) {\n'
            '                    onLoginSuccess(email)\n'
            '                } else {\n'
            '                    errorMessage = "Please enter a valid email and 6+ char password."\n'
            '                }\n'
            '            },\n'
            '            modifier = Modifier.fillMaxWidth().height(50.dp)\n'
            '        ) {\n'
            '            Text("Login")\n'
            '        }\n\n'
            '        errorMessage?.let {\n'
            '            Spacer(modifier = Modifier.height(12.dp))\n'
            '            Text(text = it, color = MaterialTheme.colorScheme.error)\n'
            '        }\n'
            '    }\n'
            '}\n'
        )

        login_file.parent.mkdir(parents=True, exist_ok=True)
        login_file.write_text(login_code, encoding="utf-8")
        return {"success": True, "file": str(login_file), "message": "Created LoginScreen.kt"}

    def add_firebase_auth(self, project_dir: str | Path, package_name: str = "com.example.nrai") -> Dict[str, Any]:
        """Adds Firebase Auth dependency and helper class."""
        self.add_dependency(project_dir, "com.google.firebase:firebase-auth-ktx:22.3.1")

        p = Path(project_dir).resolve()
        package_path = package_name.replace(".", "/")
        auth_file = p / "app" / "src" / "main" / "java" / package_path / "FirebaseAuthHelper.kt"

        auth_code = (
            f'package {package_name}\n\n'
            'class FirebaseAuthHelper {\n'
            '    fun signInWithEmail(email: String, pass: String): Boolean {\n'
            '        return email.isNotBlank() && pass.length >= 6\n'
            '    }\n'
            '    fun signOut() {}\n'
            '}\n'
        )

        auth_file.parent.mkdir(parents=True, exist_ok=True)
        auth_file.write_text(auth_code, encoding="utf-8")
        return {"success": True, "message": "Injected Firebase Auth dependency and created FirebaseAuthHelper.kt"}

    def change_button_color(self, project_dir: str | Path, color_hex: str = "0xFF00C853") -> Dict[str, Any]:
        """Modifies PrimaryButtonColor in Color.kt."""
        p = Path(project_dir).resolve()
        color_files = list(p.glob("**/Color.kt"))
        if not color_files:
            return {"success": False, "message": "Color.kt not found"}

        cf = color_files[0]
        text = cf.read_text(encoding="utf-8")
        updated = re.sub(r"val PrimaryButtonColor = Color\(0x[0-9A-Fa-f]+\)", f"val PrimaryButtonColor = Color({color_hex})", text)
        if updated == text and "val PrimaryButtonColor" not in text:
            updated += f"\nval PrimaryButtonColor = Color({color_hex})\n"

        cf.write_text(updated, encoding="utf-8")
        return {"success": True, "file": str(cf), "message": f"Updated button color to {color_hex}"}

    def list_devices(self) -> Dict[str, Any]:
        """Checks for connected Android devices or running emulators via ADB."""
        try:
            res = subprocess.run(["adb", "devices"], capture_output=True, text=True, timeout=5.0)
            lines = res.stdout.strip().splitlines()[1:]
            devices = [line.split()[0] for line in lines if line.strip() and "device" in line]
            return {"success": True, "devices": devices, "count": len(devices)}
        except Exception as e:
            return {"success": False, "devices": [], "count": 0, "message": f"ADB probe: {e}"}
