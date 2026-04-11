# Copyright (c) 2026 Mathieu Cadi — Openema SARL
# Released under the MIT License — https://github.com/openema/oeil
# Application: Oeil — Open-source AI edge camera surveillance
# Date: April 11, 2026

#!/usr/bin/env python3
"""Initialize Oeil database."""
import asyncio
import sys
sys.path.insert(0, '/opt/oeil')

from database import init_db

async def main():
    await init_db()
    print("Database initialized successfully")

asyncio.run(main())
