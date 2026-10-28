"""Prepare private, repeatable platform workspaces; never print credentials or start services."""
from __future__ import annotations

import json
import base64
from pathlib import Path
import re
import secrets
import shutil

import yaml

ROOT = Path(__file__).resolve().parents[1]


def env_values(content):
    return {line.split("=", 1)[0]: line.split("=", 1)[1] for line in content.splitlines()
            if line and not line.startswith("#") and "=" in line}


def replace_env(content, changes):
    seen = set()
    lines = []
    for line in content.splitlines():
        key = line.split("=", 1)[0]
        if key in changes and "=" in line and not line.startswith("#"):
            line = key + "=" + str(changes[key])
            seen.add(key)
        lines.append(line)
    lines.extend(key + "=" + str(value) for key, value in changes.items() if key not in seen)
    return "\n".join(lines) + "\n"


def local_ports(document):
    for service in document.get("services", {}).values():
        ports = service.get("ports", [])
        for index, port in enumerate(ports):
            if isinstance(port, str):
                normalized = re.sub(r"\$\{[^}]+\}", "variable", port)
                if normalized.startswith("127.0.0.1:") and normalized.count(":") == 2:
                    continue
                if normalized.count(":") != 1:
                    raise ValueError("Unexpected upstream port mapping; review before deployment")
                ports[index] = "127.0.0.1:" + port
            else:
                port["host_ip"] = "127.0.0.1"


def configure(name, root=ROOT):
    vendor = root / "deploy/vendor" / name / "docker"
    target = root / ".secrets/platforms" / name
    marker = target / ".icp-prepared.json"
    if marker.exists():
        return {"platform": name, "status": "existing_private_configuration_retained"}
    if not vendor.is_dir():
        raise RuntimeError("Prepare pinned upstream sources first")
    shutil.copytree(vendor, target, dirs_exist_ok=True)
    if name == "dify":
        content = (vendor / ".env.example").read_text(encoding="utf-8")
        changes = {key: secrets.token_hex(24) for key in [
            "SECRET_KEY", "DB_PASSWORD", "REDIS_PASSWORD", "SANDBOX_API_KEY",
            "PLUGIN_DAEMON_KEY", "PLUGIN_DIFY_INNER_API_KEY", "WEAVIATE_API_KEY",
            "DIFY_AGENT_API_TOKEN", "DIFY_AGENT_SERVER_SECRET_KEY", "DIFY_AGENT_LOCAL_SANDBOX_AUTH_TOKEN",
        ]}
        changes.update({
            "DIFY_AGENT_SERVER_SECRET_KEY": base64.urlsafe_b64encode(secrets.token_bytes(32)).decode().rstrip("="),
            "CODE_EXECUTION_API_KEY": changes["SANDBOX_API_KEY"],
            "DIFY_AGENT_PLUGIN_DAEMON_API_KEY": changes["PLUGIN_DAEMON_KEY"],
            "DIFY_AGENT_INNER_API_KEY": changes["PLUGIN_DIFY_INNER_API_KEY"],
            "DIFY_AGENT_SHELLCTL_AUTH_TOKEN": changes["DIFY_AGENT_LOCAL_SANDBOX_AUTH_TOKEN"],
            "CELERY_BROKER_URL": "redis://:" + changes["REDIS_PASSWORD"] + "@redis:6379/1",
            "INIT_PASSWORD": "Icp@" + secrets.token_hex(12),
            "EXPOSE_NGINX_PORT": "8081", "EXPOSE_NGINX_SSL_PORT": "8443",
            "CONSOLE_API_URL": "http://127.0.0.1:8081", "CONSOLE_WEB_URL": "http://127.0.0.1:8081",
            "SERVICE_API_URL": "http://127.0.0.1:8081", "APP_API_URL": "http://127.0.0.1:8081",
            "APP_WEB_URL": "http://127.0.0.1:8081", "FILES_URL": "http://127.0.0.1:8081",
            "CELERY_WORKER_AMOUNT": "1",
            "SSRF_PROXY_ALLOW_PRIVATE_DOMAINS": "host.docker.internal",
        })
        document = yaml.safe_load((vendor / "docker-compose.yaml").read_text(encoding="utf-8"))
        persistent = {
            "./volumes/app/storage": "icp_app_storage", "./volumes/db/data": "icp_database",
            "./volumes/redis/data": "icp_redis", "./volumes/plugin_daemon": "icp_plugins",
            "./volumes/weaviate": "icp_weaviate",
        }
        for service in document["services"].values():
            for index, mount in enumerate(service.get("volumes", [])):
                if isinstance(mount, str) and mount.split(":", 1)[0] in persistent:
                    source, suffix = mount.split(":", 1)
                    service["volumes"][index] = persistent[source] + ":" + suffix
                    document.setdefault("volumes", {})[persistent[source]] = None
    elif name == "ragflow":
        content = (vendor / ".env").read_text(encoding="utf-8")
        changes = {key: secrets.token_hex(24) for key in [
            "ELASTIC_PASSWORD", "MYSQL_PASSWORD", "MINIO_PASSWORD", "REDIS_PASSWORD",
            "SEEKDB_PASSWORD", "SERENEDB_PASSWORD", "OCEANBASE_PASSWORD", "GAUSSDB_PASSWORD",
            "GAUSSDB_METADATA_PASSWORD", "OB_SYS_PASSWORD", "OB_TENANT_PASSWORD", "CLICKHOUSE_PASSWORD",
        ]}
        changes.update({"ADMIN_DEFAULT_PASSWORD": "Icp@" + secrets.token_hex(16),
                        "OPENSEARCH_PASSWORD": "Icp@" + secrets.token_hex(16),
                        "SVR_WEB_HTTP_PORT": "8180", "SVR_WEB_HTTPS_PORT": "8444",
                        "DOC_ENGINE": "elasticsearch", "DEVICE": "cpu", "METADATA_DB_PROFILE": "mysql",
                        "MEM_LIMIT": "4294967296", "DOC_BULK_SIZE": "1", "THREAD_POOL_MAX_WORKERS": "8", "LLM_TIMEOUT_SECONDS": "90",
                        "RAGFLOW_IMAGE": "infiniflow/ragflow:v0.27.2"})
        document = yaml.safe_load((vendor / "docker-compose-base.yml").read_text(encoding="utf-8"))
        es_environment=document["services"]["es01"].setdefault("environment", {})
        if isinstance(es_environment,list):
            es_environment[:]=[value for value in es_environment if not value.startswith("ES_JAVA_OPTS=")]
            es_environment.append("ES_JAVA_OPTS=-Xms2g -Xmx2g")
        else:
            es_environment["ES_JAVA_OPTS"]="-Xms2g -Xmx2g"
        main = yaml.safe_load((vendor / "docker-compose.yml").read_text(encoding="utf-8"))
        for key, value in main.items():
            if key == "include":
                continue
            if isinstance(value, dict):
                document.setdefault(key, {}).update(value)
            else:
                document[key] = value
        for service in document["services"].values():
            for index, mount in enumerate(service.get("volumes", [])):
                if isinstance(mount, str) and mount.startswith("./ragflow-logs:"):
                    service["volumes"][index] = "icp_logs:" + mount.split(":", 1)[1]
                    document.setdefault("volumes", {})["icp_logs"] = None
        for service_name in ["ragflow-cpu","ragflow-gpu"]:
            if service_name in document["services"]:
                document["services"][service_name]["command"]=["--disable-datasync","--init-model-provider-tables"]
    else:
        raise ValueError("Unsupported platform")
    local_ports(document)
    (target / ".env").write_text(replace_env(content, changes), encoding="utf-8")
    (target / "icp.compose.yaml").write_text(yaml.safe_dump(document, allow_unicode=True, sort_keys=False), encoding="utf-8")
    report = {"platform": name, "status": "prepared_not_started", "compose": "icp.compose.yaml",
              "host_binding": "127.0.0.1", "storage": "critical data in Docker named volumes",
              "validation": "Python YAML checks only; Docker Compose and live services pending"}
    marker.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


if __name__ == "__main__":
    try:
        for platform in ["dify", "ragflow"]:
            print(json.dumps(configure(platform), ensure_ascii=False))
    except Exception as exc:
        print("Private platform preparation failed. Error type=" + type(exc).__name__)
        raise SystemExit(1)
