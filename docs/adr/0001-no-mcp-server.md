# No MCP server

The plugin ships no MCP server — everything is skills, agents, and Python scripts invoked through Bash. This was a real option (an MCP server could expose vault queries as structured tools), but plugin subagents ignore inline `mcpServers` frontmatter for security, so a bundled server's tools would load globally for every user rather than being scoped to `wiki-researcher`. A server would also add prefix tool-definition tokens and a process to manage, and only MCP-providing plugins invalidate the install-time cache.

## Consequences

If a genuine external integration appears later (a live API the filesystem can't reach), that's the moment to reconsider — and it belongs outside the plugin, connected by the user, not bundled into it.
