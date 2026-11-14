"""Scout mode: UWB pucks thrown into a room self-locate, register into the twin and scan as a multistatic radar.

Modules (see docs/SCOUT_CONTRACT.md): geometry, scene, sim, dsp (radar engine); register, mismatch (twin
registration and comparison); session (the pipeline, pacing, the Scan object, the agent's brief); feeds
(GeoJSON and Cursor-on-Target); shadow, source_kit (real boards). Nothing is imported here on purpose, so
that a broken engine module can never stop the server from loading: `session` imports the engine lazily.
"""
