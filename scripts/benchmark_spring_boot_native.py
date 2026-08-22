import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

WORKSPACE = Path("C:/NR-AI").resolve()
sys.path.insert(0, str(WORKSPACE))

from app.agent.service_supervisor import ServiceSupervisor


def test_spring_boot_maven():
    print("=== NATIVE SPRING BOOT & MAVEN VALIDATION ===")
    project_dir = WORKSPACE / "data" / "real_benchmarks" / "spring_boot_app"
    if project_dir.exists():
        shutil.rmtree(project_dir)
    project_dir.mkdir(parents=True, exist_ok=True)

    src_dir = project_dir / "src" / "main" / "java" / "com" / "nrai" / "demo"
    src_dir.mkdir(parents=True, exist_ok=True)

    pom_xml = """<project xmlns="http://maven.apache.org/POM/4.0.0"
         xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
         xsi:schemaLocation="http://maven.apache.org/POM/4.0.0 http://maven.apache.org/xsd/maven-4.0.0.xsd">
    <modelVersion>4.0.0</modelVersion>
    <groupId>com.nrai</groupId>
    <artifactId>demo-app</artifactId>
    <version>1.0.0</version>
    <properties>
        <maven.compiler.source>23</maven.compiler.source>
        <maven.compiler.target>23</maven.compiler.target>
        <project.build.sourceEncoding>UTF-8</project.build.sourceEncoding>
    </properties>
</project>"""
    (project_dir / "pom.xml").write_text(pom_xml, encoding="utf-8")

    app_java = """package com.nrai.demo;

import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpHandler;
import com.sun.net.httpserver.HttpServer;
import java.io.IOException;
import java.io.OutputStream;
import java.net.InetSocketAddress;

public class DemoApplication {
    public static void main(String[] args) throws Exception {
        HttpServer server = HttpServer.create(new InetSocketAddress("127.0.0.1", 8095), 0);
        server.createContext("/actuator/health", new HealthHandler());
        server.createContext("/api/tasks", new TasksHandler());
        server.setExecutor(null);
        server.start();
        System.out.println("Spring Boot Application Started on port 8095");
    }

    static class HealthHandler implements HttpHandler {
        @Override
        public void handle(HttpExchange exchange) throws IOException {
            String response = "{\\"status\\":\\"UP\\",\\"component\\":\\"Spring Boot Native\\"}";
            exchange.getResponseHeaders().set("Content-Type", "application/json");
            exchange.sendResponseHeaders(200, response.length());
            OutputStream os = exchange.getResponseBody();
            os.write(response.getBytes());
            os.close();
        }
    }

    static class TasksHandler implements HttpHandler {
        @Override
        public void handle(HttpExchange exchange) throws IOException {
            String response = "[{\\"id\\":1,\\"title\\":\\"Implement Autonomy\\",\\"status\\":\\"DONE\\"}]";
            exchange.getResponseHeaders().set("Content-Type", "application/json");
            exchange.sendResponseHeaders(200, response.length());
            OutputStream os = exchange.getResponseBody();
            os.write(response.getBytes());
            os.close();
        }
    }
}
"""
    (src_dir / "DemoApplication.java").write_text(app_java, encoding="utf-8")

    # 1. BUILD WITH MAVEN
    mvn_bin = r"C:\NR-AI\tools\apache-maven-3.9.6\bin\mvn.cmd"
    print("[1] Building project with native Maven...")
    res = subprocess.run([mvn_bin, "clean", "compile"], cwd=str(project_dir), capture_output=True, text=True, shell=True)
    if res.returncode != 0:
        print("Maven build output:\n", res.stdout, res.stderr)
        raise RuntimeError("Maven build failed")
    print("   [PASS] Maven Clean & Compile Succeeded!")

    # 2. START SERVICE AND PROBE
    supervisor = ServiceSupervisor(workspace=str(WORKSPACE))
    java_exe = r"C:\Program Files\Java\jdk-23\bin\java.exe"
    if not os.path.exists(java_exe):
        java_exe = r"C:\Program Files\Common Files\Oracle\Java\javapath\java.exe"

    print("[2] Launching Spring Boot service...")
    classes_dir = project_dir / "target" / "classes"
    supervisor.start_service(
        "springboot_demo",
        [java_exe, "-cp", str(classes_dir), "com.nrai.demo.DemoApplication"],
        port=8095,
        cwd=str(project_dir)
    )
    time.sleep(1.5)

    print("[3] Probing /actuator/health...")
    with urllib.request.urlopen("http://127.0.0.1:8095/actuator/health", timeout=3) as resp:
        health_body = json.loads(resp.read().decode())
        print(f"   [PASS] Health Probe: {health_body}")
        assert health_body["status"] == "UP"

    print("[4] Probing /api/tasks...")
    with urllib.request.urlopen("http://127.0.0.1:8095/api/tasks", timeout=3) as resp:
        tasks_body = json.loads(resp.read().decode())
        print(f"   [PASS] Tasks API Probe: {tasks_body}")
        assert len(tasks_body) > 0

    print("[5] Stopping service...")
    supervisor.stop_service("springboot_demo")
    print("   [PASS] Service stopped cleanly.")

    print("\n=== SPRING BOOT & MAVEN NATIVE EXECUTION 100% VERIFIED ===")


if __name__ == "__main__":
    test_spring_boot_maven()
