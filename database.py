import secrets
import string
from datetime import datetime, timezone

from motor.motor_asyncio import AsyncIOMotorClient
from pymongo.errors import DuplicateKeyError

import config


def _now():
    return datetime.now(timezone.utc)


def _gen_code(length: int = 8) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


class Database:
    def __init__(self):
        self.client = AsyncIOMotorClient(config.MONGO_URI)
        self.db = self.client[config.DB_NAME]

        self.users = self.db.users
        self.giveaways = self.db.giveaways
        self.participants = self.db.participants
        self.votes = self.db.votes
        self.settings = self.db.settings

    async def init_indexes(self):
        await self.giveaways.create_index("code", unique=True)
        await self.participants.create_index(
            [("giveaway_id", 1), ("user_id", 1)], unique=True
        )
        await self.votes.create_index(
            [("giveaway_id", 1), ("voter_id", 1)], unique=True
        )

    # ── Users ────────────────────────────────────────────────────────
    async def add_user(self, user_id: int, username: str | None, first_name: str):
        await self.users.update_one(
            {"_id": user_id},
            {
                "$set": {"username": username, "first_name": first_name},
                "$setOnInsert": {"joined_at": _now()},
            },
            upsert=True,
        )

    async def get_all_user_ids(self) -> list[int]:
        cursor = self.users.find({}, {"_id": 1})
        return [doc["_id"] async for doc in cursor]

    async def get_user_count(self) -> int:
        return await self.users.count_documents({})

    # ── Giveaways ────────────────────────────────────────────────────
    async def create_giveaway(
        self,
        owner_id: int,
        channel_id: int,
        channel_title: str,
        channel_username: str | None,
    ) -> dict:
        for _ in range(6):
            code = _gen_code()
            doc = {
                "code": code,
                "owner_id": owner_id,
                "channel_id": channel_id,
                "channel_title": channel_title,
                "channel_username": channel_username,
                "status": "active",
                "join_message_id": None,
                "participant_count": 0,
                "total_votes": 0,
                "created_at": _now(),
                "ended_at": None,
            }
            try:
                result = await self.giveaways.insert_one(doc)
                doc["_id"] = result.inserted_id
                return doc
            except DuplicateKeyError:
                continue
        raise RuntimeError("Could not generate a unique giveaway code")

    async def get_giveaway_by_code(self, code: str):
        return await self.giveaways.find_one({"code": code})

    async def get_giveaway(self, giveaway_id):
        return await self.giveaways.find_one({"_id": giveaway_id})

    async def get_active_giveaways_by_owner(self, owner_id: int) -> list[dict]:
        cursor = self.giveaways.find({"owner_id": owner_id, "status": "active"})
        return [doc async for doc in cursor]

    async def set_join_message(self, giveaway_id, message_id: int):
        await self.giveaways.update_one(
            {"_id": giveaway_id}, {"$set": {"join_message_id": message_id}}
        )

    async def end_giveaway(self, giveaway_id):
        await self.giveaways.update_one(
            {"_id": giveaway_id},
            {"$set": {"status": "ended", "ended_at": _now()}},
        )

    # ── Participants ─────────────────────────────────────────────────
    async def add_participant(
        self, giveaway_id, user_id: int, name: str, username: str | None
    ):
        """Returns (participant_doc, is_new)."""
        try:
            doc = {
                "giveaway_id": giveaway_id,
                "user_id": user_id,
                "name": name,
                "username": username,
                "votes": 0,
                "entry_message_id": None,
                "joined_at": _now(),
            }
            result = await self.participants.insert_one(doc)
            doc["_id"] = result.inserted_id
            await self.giveaways.update_one(
                {"_id": giveaway_id}, {"$inc": {"participant_count": 1}}
            )
            return doc, True
        except DuplicateKeyError:
            existing = await self.participants.find_one(
                {"giveaway_id": giveaway_id, "user_id": user_id}
            )
            return existing, False

    async def get_participant(self, participant_id):
        return await self.participants.find_one({"_id": participant_id})

    async def get_participant_by_user(self, giveaway_id, user_id: int):
        return await self.participants.find_one(
            {"giveaway_id": giveaway_id, "user_id": user_id}
        )

    async def set_entry_message(self, participant_id, message_id: int):
        await self.participants.update_one(
            {"_id": participant_id}, {"$set": {"entry_message_id": message_id}}
        )

    async def list_participants(self, giveaway_id, limit: int = 25) -> list[dict]:
        cursor = (
            self.participants.find({"giveaway_id": giveaway_id})
            .sort("joined_at", 1)
            .limit(limit)
        )
        return [doc async for doc in cursor]

    async def get_leaderboard(self, giveaway_id, top: int = 10) -> list[dict]:
        cursor = (
            self.participants.find({"giveaway_id": giveaway_id})
            .sort("votes", -1)
            .limit(top)
        )
        return [doc async for doc in cursor]

    async def get_participant_count(self, giveaway_id) -> int:
        return await self.participants.count_documents({"giveaway_id": giveaway_id})

    async def get_top_participant(self, giveaway_id):
        cursor = (
            self.participants.find({"giveaway_id": giveaway_id})
            .sort("votes", -1)
            .limit(1)
        )
        results = [doc async for doc in cursor]
        return results[0] if results else None

    # ── Votes ────────────────────────────────────────────────────────
    async def cast_vote(self, giveaway_id, participant_id, voter_id: int) -> bool:
        try:
            await self.votes.insert_one(
                {
                    "giveaway_id": giveaway_id,
                    "participant_id": participant_id,
                    "voter_id": voter_id,
                    "created_at": _now(),
                }
            )
        except DuplicateKeyError:
            return False
        await self.participants.update_one(
            {"_id": participant_id}, {"$inc": {"votes": 1}}
        )
        await self.giveaways.update_one(
            {"_id": giveaway_id}, {"$inc": {"total_votes": 1}}
        )
        return True

    async def adjust_vote(self, giveaway_id, participant_id, delta: int):
        await self.participants.update_one(
            {"_id": participant_id}, {"$inc": {"votes": delta}}
        )
        await self.giveaways.update_one(
            {"_id": giveaway_id}, {"$inc": {"total_votes": delta}}
        )
        participant = await self.participants.find_one({"_id": participant_id})
        if participant and participant["votes"] < 0:
            diff = participant["votes"]
            await self.participants.update_one(
                {"_id": participant_id}, {"$set": {"votes": 0}}
            )
            await self.giveaways.update_one(
                {"_id": giveaway_id}, {"$inc": {"total_votes": -diff}}
            )

    # ── Settings (global, key/value) ────────────────────────────────
    async def get_setting(self, key: str, default=None):
        doc = await self.settings.find_one({"_id": key})
        return doc["value"] if doc else default

    async def set_setting(self, key: str, value):
        await self.settings.update_one(
            {"_id": key}, {"$set": {"value": value}}, upsert=True
        )

    # ── Stats ────────────────────────────────────────────────────────
    async def get_stats(self) -> dict:
        return {
            "users": await self.get_user_count(),
            "giveaways_total": await self.giveaways.count_documents({}),
            "giveaways_active": await self.giveaways.count_documents(
                {"status": "active"}
            ),
            "participants_total": await self.participants.count_documents({}),
            "votes_total": await self.votes.count_documents({}),
        }


db = Database()