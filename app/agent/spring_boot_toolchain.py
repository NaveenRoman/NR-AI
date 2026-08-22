import json
import os
import re
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.agent.code_writer import CodeWriter


class SpringBootProjectDetector:
    """
    Detects Spring Boot Java projects, build tools (Maven / Gradle), and starter dependencies.
    """

    @staticmethod
    def detect(project_dir: str | Path) -> Dict[str, Any]:
        p = Path(project_dir).resolve()
        if not p.exists() or not p.is_dir():
            return {"is_spring_boot": False, "reason": "Directory does not exist"}

        has_pom = (p / "pom.xml").exists()
        has_gradle = (p / "build.gradle").exists() or (p / "build.gradle.kts").exists()
        has_src_main = (p / "src" / "main" / "java").exists()

        is_spring_boot = False
        build_tool = "unknown"
        if has_pom:
            build_tool = "maven"
            try:
                pom_txt = (p / "pom.xml").read_text(encoding="utf-8", errors="ignore")
                if "spring-boot" in pom_txt or "org.springframework.boot" in pom_txt:
                    is_spring_boot = True
            except Exception:
                pass
        elif has_gradle:
            build_tool = "gradle"
            try:
                g_file = p / "build.gradle" if (p / "build.gradle").exists() else p / "build.gradle.kts"
                g_txt = g_file.read_text(encoding="utf-8", errors="ignore")
                if "spring" in g_txt:
                    is_spring_boot = True
            except Exception:
                pass

        if not is_spring_boot and has_src_main:
            # Check for @SpringBootApplication in any java file
            for jf in p.glob("src/main/java/**/*.java"):
                try:
                    if "@SpringBootApplication" in jf.read_text(encoding="utf-8", errors="ignore"):
                        is_spring_boot = True
                        break
                except Exception:
                    pass

        return {
            "is_spring_boot": is_spring_boot,
            "project_path": str(p),
            "project_name": p.name,
            "build_tool": build_tool,
            "has_src_main": has_src_main,
        }


class SpringBootProjectInspector:
    """
    Deep inspection of Spring Boot projects (controllers, services, entities, pom dependencies).
    """

    def __init__(self, project_dir: str | Path):
        self.project_dir = Path(project_dir).resolve()

    def inspect(self) -> Dict[str, Any]:
        detection = SpringBootProjectDetector.detect(self.project_dir)
        if not detection.get("is_spring_boot"):
            return {
                "success": False,
                "is_spring_boot": False,
                "message": detection.get("reason", "Not a Spring Boot project"),
            }

        java_files = [str(f.relative_to(self.project_dir)).replace("\\", "/") for f in self.project_dir.glob("src/**/*.java")]
        controllers = [f for f in java_files if "controller" in f.lower()]
        services = [f for f in java_files if "service" in f.lower()]
        entities = [f for f in java_files if "model" in f.lower() or "entity" in f.lower()]
        tests = [f for f in java_files if "test" in f.lower()]

        dependencies = self._parse_pom_dependencies()

        return {
            "success": True,
            "is_spring_boot": True,
            "project_name": detection["project_name"],
            "project_path": str(self.project_dir),
            "build_tool": detection["build_tool"],
            "total_java_files": len(java_files),
            "controllers": controllers,
            "services": services,
            "entities": entities,
            "tests": tests,
            "dependencies": dependencies,
        }

    def _parse_pom_dependencies(self) -> List[str]:
        pom = self.project_dir / "pom.xml"
        if not pom.exists():
            return []
        try:
            txt = pom.read_text(encoding="utf-8", errors="ignore")
            return re.findall(r"<artifactId>([a-zA-Z0-9_\-]+)</artifactId>", txt)
        except Exception:
            return []


class SpringBootErrorAnalyzer:
    """
    Parses Java / Spring Boot compiler and runtime exception logs.
    """

    @staticmethod
    def analyze(log: str) -> Dict[str, Any]:
        text = str(log or "")

        # 1. Java compilation error: e.g., src/main/java/com/example/demo/controller/AccountController.java:12: error: cannot find symbol
        compile_match = re.search(r"([^\r\n:]+\.java):(\d+):\s+error:\s+([^\r\n]+)", text)
        if compile_match:
            filepath, line_no, msg = compile_match.groups()
            return {
                "category": "java_compiler",
                "file": filepath.strip(),
                "line": int(line_no),
                "error": msg.strip(),
                "diagnosis": f"Java compiler error: {msg.strip()}",
                "suggestion": "Check import statements, variable declarations, or method names in Java source.",
            }

        # 2. UnsatisfiedDependencyException / NoSuchBeanDefinitionException
        if "NoSuchBeanDefinitionException" in text or "UnsatisfiedDependencyException" in text:
            return {
                "category": "spring_dependency_injection",
                "file": "src/main/java",
                "error": "Spring Bean autowiring or dependency injection failure.",
                "diagnosis": "A required Spring component (@Service, @Repository, @Component) was not found in component scan.",
                "suggestion": "Add @Service or @Repository annotation to the implementation class.",
            }

        return {
            "category": "unknown_spring_error",
            "file": "src",
            "error": text[:300].strip(),
            "diagnosis": "Spring Boot build or runtime error.",
            "suggestion": "Inspect Maven pom.xml configuration and Java source files.",
        }


class SpringBootToolchain:
    """
    Spring Boot Java backend toolchain manager for NR-AI.
    """

    def __init__(self, workspace: Optional[str] = None):
        self.workspace = Path(workspace or os.getcwd()).resolve()
        self.writer = CodeWriter(workspace=str(self.workspace))

    def scaffold_project(
        self,
        project_name: str = "springboot_app",
        target_dir: Optional[str] = None,
        package_name: str = "com.example.nrai",
        domain: str = "banking",
    ) -> Dict[str, Any]:
        """Scaffolds complete Spring Boot project architecture."""
        safe_name = re.sub(r"[^a-zA-Z0-9_]", "", project_name)
        root = (self.workspace / (target_dir or f"data/{safe_name.lower()}")).resolve()
        root.mkdir(parents=True, exist_ok=True)

        pkg_path = package_name.replace(".", "/")
        src_java = root / "src" / "main" / "java" / pkg_path
        src_res = root / "src" / "main" / "resources"
        src_test = root / "src" / "test" / "java" / pkg_path

        # 1. pom.xml
        pom_xml = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<project xmlns="http://maven.apache.org/POM/4.0.0"\n'
            '         xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"\n'
            '         xsi:schemaLocation="http://maven.apache.org/POM/4.0.0 https://maven.apache.org/xsd/maven-4.0.0.xsd">\n'
            '    <modelVersion>4.0.0</modelVersion>\n'
            '    <parent>\n'
            '        <groupId>org.springframework.boot</groupId>\n'
            '        <artifactId>spring-boot-starter-parent</artifactId>\n'
            '        <version>3.2.3</version>\n'
            '        <relativePath/>\n'
            '    </parent>\n'
            f'    <groupId>{package_name}</groupId>\n'
            f'    <artifactId>{safe_name.lower()}</artifactId>\n'
            '    <version>0.0.1-SNAPSHOT</version>\n'
            f'    <name>{safe_name}</name>\n'
            '    <description>Spring Boot Microservice built by NR AI</description>\n'
            '    <properties>\n'
            '        <java.version>17</java.version>\n'
            '    </properties>\n'
            '    <dependencies>\n'
            '        <dependency>\n'
            '            <groupId>org.springframework.boot</groupId>\n'
            '            <artifactId>spring-boot-starter-web</artifactId>\n'
            '        </dependency>\n'
            '        <dependency>\n'
            '            <groupId>org.springframework.boot</groupId>\n'
            '            <artifactId>spring-boot-starter-data-jpa</artifactId>\n'
            '        </dependency>\n'
            '        <dependency>\n'
            '            <groupId>com.h2database</groupId>\n'
            '            <artifactId>h2</artifactId>\n'
            '            <scope>runtime</scope>\n'
            '        </dependency>\n'
            '        <dependency>\n'
            '            <groupId>org.springframework.boot</groupId>\n'
            '            <artifactId>spring-boot-starter-test</artifactId>\n'
            '            <scope>test</scope>\n'
            '        </dependency>\n'
            '    </dependencies>\n'
            '</project>\n'
        )

        # 2. application.properties
        app_props = (
            "spring.application.name=" + safe_name.lower() + "\n"
            "server.port=8080\n"
            "spring.datasource.url=jdbc:h2:mem:nraidb;DB_CLOSE_DELAY=-1\n"
            "spring.datasource.driverClassName=org.h2.Driver\n"
            "spring.datasource.username=sa\n"
            "spring.datasource.password=\n"
            "spring.jpa.database-platform=org.hibernate.dialect.H2Dialect\n"
            "spring.jpa.hibernate.ddl-auto=update\n"
        )

        # 3. Application.java
        app_java = (
            f"package {package_name};\n\n"
            "import org.springframework.boot.SpringApplication;\n"
            "import org.springframework.boot.autoconfigure.SpringBootApplication;\n\n"
            "@SpringBootApplication\n"
            "public class Application {\n"
            "    public static void main(String[] args) {\n"
            "        SpringApplication.run(Application.class, args);\n"
            "    }\n"
            "}\n"
        )

        # 4. Account.java (Entity)
        entity_java = (
            f"package {package_name}.model;\n\n"
            "import jakarta.persistence.*;\n\n"
            "@Entity\n"
            "@Table(name = \"accounts\")\n"
            "public class Account {\n"
            "    @Id\n"
            "    @GeneratedValue(strategy = GenerationType.IDENTITY)\n"
            "    private Long id;\n"
            "    private String accountNumber;\n"
            "    private Double balance;\n"
            "    private String ownerName;\n\n"
            "    public Account() {}\n\n"
            "    public Account(String accountNumber, Double balance, String ownerName) {\n"
            "        this.accountNumber = accountNumber;\n"
            "        this.balance = balance;\n"
            "        this.ownerName = ownerName;\n"
            "    }\n\n"
            "    public Long getId() { return id; }\n"
            "    public String getAccountNumber() { return accountNumber; }\n"
            "    public void setAccountNumber(String num) { this.accountNumber = num; }\n"
            "    public Double getBalance() { return balance; }\n"
            "    public void setBalance(Double balance) { this.balance = balance; }\n"
            "    public String getOwnerName() { return ownerName; }\n"
            "    public void setOwnerName(String name) { this.ownerName = name; }\n"
            "}\n"
        )

        # 5. AccountController.java
        controller_java = (
            f"package {package_name}.controller;\n\n"
            f"import {package_name}.model.Account;\n"
            "import org.springframework.http.ResponseEntity;\n"
            "import org.springframework.web.bind.annotation.*;\n"
            "import java.util.*;\n\n"
            "@RestController\n"
            "@RequestMapping(\"/api/accounts\")\n"
            "@CrossOrigin(origins = \"*\")\n"
            "public class AccountController {\n"
            "    private final List<Account> accounts = new ArrayList<>();\n\n"
            "    @GetMapping\n"
            "    public List<Account> getAllAccounts() {\n"
            "        if (accounts.isEmpty()) {\n"
            "            accounts.add(new Account(\"ACC-1001\", 5000.0, \"John Doe\"));\n"
            "            accounts.add(new Account(\"ACC-1002\", 12500.5, \"Jane Smith\"));\n"
            "        }\n"
            "        return accounts;\n"
            "    }\n\n"
            "    @PostMapping\n"
            "    public ResponseEntity<Account> createAccount(@RequestBody Account account) {\n"
            "        accounts.add(account);\n"
            "        return ResponseEntity.ok(account);\n"
            "    }\n"
            "}\n"
        )

        # 6. ApplicationTests.java
        test_java = (
            f"package {package_name};\n\n"
            "import org.junit.jupiter.api.Test;\n"
            "import static org.junit.jupiter.api.Assertions.*;\n\n"
            "class ApplicationTests {\n"
            "    @Test\n"
            "    void contextLoads() {\n"
            "        assertTrue(true);\n"
            "    }\n"
            "}\n"
        )

        # Write files
        files_to_write = {
            root / "pom.xml": pom_xml,
            src_res / "application.properties": app_props,
            src_java / "Application.java": app_java,
            src_java / "model" / "Account.java": entity_java,
            src_java / "controller" / "AccountController.java": controller_java,
            src_test / "ApplicationTests.java": test_java,
        }

        for path, content in files_to_write.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")

        return {
            "success": True,
            "project_name": safe_name,
            "project_path": str(root),
            "domain": domain,
            "files_created": len(files_to_write),
        }
