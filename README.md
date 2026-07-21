# Unity Context Slicer

**Unity Context Slicer** is a graph-based context extraction engine and Model Context Protocol (MCP) server designed for Unity projects. It parses C# scripts, Unity scenes (`.unity`), prefabs (`.prefab`), and `.meta` asset GUIDs into an in-memory knowledge graph. By slicing targeted $N$-hop neighborhoods around relevant components and compressing raw YAML/AST structures, it delivers **5–10× token reduction**, producing compact context bundles optimized for local and cloud LLMs.

---

## 🔑 Key Features

* **🕸️ Graph-Based Project Indexing**: Maps C# classes, methods, events, Unity GameObjects, MonoBehaviours, scenes, and prefabs into a unified directed graph.
* **⚡ 5–10× Context Compression**: Converts verbose scene YAML and code structures into dense, high-information text representations suited for restricted LLM context windows (e.g., 7B local models).
* **🎯 Focused Slicing**: Extracts $N$-hop relational neighborhoods (`unity_slice`) or comprehensive class context cards (`unity_class`) including method callers, attached scene objects, and inheritance hierarchies.
* **🔄 Auto-Reloading Resident Session**: Monitors file modification times (`mtime`) across `.cs`, `.unity`, and `.prefab` files to update the resident graph in milliseconds upon code changes.
* **🔌 Built-in MCP Server**: Exposes stdio-based MCP tools for direct integration with MCP clients such as Claude Desktop, Cursor, or custom AI agents.
* **🆔 Unity GUID Resolution**: Resolves `.meta` file GUIDs to bridge C# MonoBehaviours with serialized scene/prefab component references.

---

## 🛠️ Architecture & Pipeline

```mermaid
graph TD
    A["Unity Project Directory"] --> B["C# Roslyn Scanner / YAML Parser"]
    A --> C["Meta GUID Resolver"]
    B --> D["Loader & In-Memory Graph"]
    C --> D
    D --> E["Session Manager"]
    E --> F["Graph Slicer"]
    F --> G["Compressor & Task Bundler"]
    G --> H["MCP Server & Prompt Bundles"]
```

### Pipeline Steps
1. **Parsing & Resolution**: C# AST analysis via Roslyn ([ScannerCore.cs](file:///d:/MyGames/UnitySkill/unity_context_slicer/csharp_scanner/ScannerCore.cs)) combined with Python-native Unity scene/prefab parsing ([unity_parser.py](file:///d:/MyGames/UnitySkill/unity_context_slicer/unity_parser.py)) and GUID resolution ([meta_resolver.py](file:///d:/MyGames/UnitySkill/unity_context_slicer/meta_resolver.py)).
2. **Graph Construction**: Builds a unified node/edge model in [ProjectGraph](file:///d:/MyGames/UnitySkill/unity_context_slicer/loader.py#L48-L100) indexed for bidirectional lookup.
3. **Neighborhood Slicing**: [SubGraph](file:///d:/MyGames/UnitySkill/unity_context_slicer/slicer.py) algorithms isolate relevant subgraphs based on class, method, or GameObject seeds.
4. **Dense Compression**: Renders subgraphs into task-ready prompt context via [compressor.py](file:///d:/MyGames/UnitySkill/unity_context_slicer/compressor.py).

---

## 🔌 MCP Tools Provided

| Tool Name | Description |
| :--- | :--- |
| `unity_search` | Search for project graph nodes by name substring and optional node type (`class`, `method`, `scene`, `prefab`, etc.). |
| `unity_class` | Retrieve structured class context (methods, callers, attached GameObjects/scenes, inheritance). |
| `unity_slice` | Extract an $N$-hop neighborhood graph surrounding a target seed node. |
| `unity_bundle` | Generate a full LLM coding prompt context bundle combining task requirements, graph context, and constraints. |
| `unity_stats` | View graph node and edge count statistics. |
| `unity_reload` | Force-reload the project graph from disk. |

---

## 📁 Repository Structure

* [unity_context_slicer/](file:///d:/MyGames/UnitySkill/unity_context_slicer)
  * [mcp_server.py](file:///d:/MyGames/UnitySkill/unity_context_slicer/mcp_server.py) — FastMCP stdio server implementation and tool definitions.
  * [loader.py](file:///d:/MyGames/UnitySkill/unity_context_slicer/loader.py) — Data primitives ([GraphNode](file:///d:/MyGames/UnitySkill/unity_context_slicer/loader.py#L22-L31), [GraphEdge](file:///d:/MyGames/UnitySkill/unity_context_slicer/loader.py#L33-L46)) and graph indexing.
  * [slicer.py](file:///d:/MyGames/UnitySkill/unity_context_slicer/slicer.py) — Neighborhood traversal and class context extraction.
  * [compressor.py](file:///d:/MyGames/UnitySkill/unity_context_slicer/compressor.py) — Dense text formatting and LLM context reduction.
  * [session.py](file:///d:/MyGames/UnitySkill/unity_context_slicer/session.py) — Resident [GraphSession](file:///d:/MyGames/UnitySkill/unity_context_slicer/session.py#L22-L100) manager with stale file detection.
  * [unity_parser.py](file:///d:/MyGames/UnitySkill/unity_context_slicer/unity_parser.py) — Scene and prefab document structure extractor.
  * [meta_resolver.py](file:///d:/MyGames/UnitySkill/unity_context_slicer/meta_resolver.py) — Mapping between `.meta` GUIDs and C# source files.
  * [annotations.py](file:///d:/MyGames/UnitySkill/unity_context_slicer/annotations.py) — Annotation cache for node purpose metadata.
  * [task_log.py](file:///d:/MyGames/UnitySkill/unity_context_slicer/task_log.py) — Task history parser and context embedder.
  * [csharp_scanner/](file:///d:/MyGames/UnitySkill/unity_context_slicer/csharp_scanner) — C# Roslyn scanner project ([CSharpScanner.csproj](file:///d:/MyGames/UnitySkill/unity_context_slicer/csharp_scanner/CSharpScanner.csproj)).

---

## 🚀 Quick Start

### Running the MCP Server
```bash
python -m unity_context_slicer.mcp_server --project-dir "path/to/UnityProject"
```

### Testing with MCP Inspector
```bash
npx -y @modelcontextprotocol/inspector python -m unity_context_slicer.mcp_server --project-dir "path/to/UnityProject"
```
