"""Verify a packaged MCP server through the public stdio protocol."""
import argparse
import asyncio
import json
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def verify(command: str, arguments: list[str], live: bool):
    async with stdio_client(StdioServerParameters(command=command, args=arguments)) as (read, write):
        async with ClientSession(read, write) as session:
            initialized = await session.initialize()
            listed = await session.list_tools()
            names = {t.name for t in listed.tools}
            required = {'navis_health', 'navis_analyze', 'navis_snapshot', 'navis_compare_snapshot', 'navis_analysis_state'}
            assert required <= names, required - names
            assert all(t.annotations is not None for t in listed.tools)
            output = await session.call_tool('navis_output_policy', {})
            assert not output.isError, output
            if live:
                health = await session.call_tool('navis_health', {})
                assert not health.isError, health
                text = next(c.text for c in health.content if c.type == 'text')
                data = json.loads(text)
                assert 'error' not in data, data
            return {'server': initialized.serverInfo.model_dump(), 'tools': len(names),
                    'initialize': 'passed', 'tools_list': 'passed', 'tools_call': 'passed', 'live': live}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true')
    parser.add_argument('command')
    parser.add_argument('arguments', nargs='*')
    args = parser.parse_args()
    print(json.dumps(asyncio.run(asyncio.wait_for(verify(args.command, args.arguments, args.live), timeout=60)), indent=2))
