"""Create private backend configuration without printing any credential."""
from pathlib import Path
import argparse
import secrets
from urllib.parse import quote

from dotenv import dotenv_values

parser=argparse.ArgumentParser()
parser.add_argument('--postgres',action='store_true',help='Select this project Docker PostgreSQL and Neo4j endpoints privately')
args=parser.parse_args()

ROOT=Path(__file__).resolve().parents[1]
target=ROOT/".env"
if target.exists():
    print("Existing .env retained; no values printed or overwritten.")
else:
    content=(ROOT/".env.example").read_text(encoding="utf-8")
    for name in ["INTERNAL_TOOL_TOKEN","POSTGRES_PASSWORD","NEO4J_PASSWORD"]:
        content=content.replace(name+"=\n",name+"="+secrets.token_hex(24)+"\n")
    # Keep model disabled until a real local /api/chat smoke test succeeds.
    content=content.replace("LLM_MODEL=qwen3:8b", "LLM_MODEL=")
    content=content.replace("DATABASE_URL=\n", "# DATABASE_URL is unset until PostgreSQL is selected.\n")
    target.write_text(content,encoding="utf-8")
    print("Private .env created; credentials are not displayed. Model enablement awaits smoke test.")

if args.postgres:
    from configure_platforms import replace_env
    values=dotenv_values(target)
    password=values.get('POSTGRES_PASSWORD')
    if not password: raise SystemExit('POSTGRES_PASSWORD is missing; configuration retained.')
    changes={'DATABASE_URL':'postgresql+psycopg://icp:'+quote(password,safe='')+'@127.0.0.1:5433/icp',
             'READ_KB_FROM_POSTGRES':'true','NEO4J_URI':'bolt://127.0.0.1:7687','GRAPH_BACKEND':'neo4j'}
    target.write_text(replace_env(target.read_text(encoding='utf-8'),changes),encoding='utf-8')
    print('Project PostgreSQL/Neo4j endpoints selected privately. Publish and verify before running the app.')

