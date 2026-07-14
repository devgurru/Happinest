"""
Predefined chip pools per stage.
These are injected into AI prompts so the model can suggest relevant chips
from known vocabulary rather than inventing random ones.

Frontend renders suggestions returned by AI. User can pick, dismiss, or type their own.
Only a conversation_turn (explicit confirmation) triggers backend/AI — not chip clicks.
"""

CHIP_POOLS: dict[str, list[str]] = {

    # S3 — Personality & Cultural Texture
    "s3_personality": [
        # Relationship type
        "College sweethearts", "Childhood sweethearts", "Work colleagues",
        "Long-distance couple", "Arranged match", "DU sweethearts",
        # Lifestyle
        "Foodies", "Travel lovers", "Dog people", "Cat people", "Fitness freaks",
        "Night owls", "Early risers", "Beach lovers", "Mountain people",
        # Cultural / community
        "Old Delhi roots", "South Mumbai crowd", "Punjabi family",
        "Bengali roots", "South Indian traditions", "NRI couple",
        "Inter-cultural", "Multi-faith family",
        # Interests
        "Music-obsessed", "Book lovers", "Art & design people",
        "Architects", "Tech founders", "Academics", "Performers",
        "Outdoor adventurers", "Home cooks",
        # Values
        "Family-first", "Close friend circle", "Privacy-focused",
        "Sustainability-minded", "Community-driven",
    ],

    # S4 — Vibe & Emotional Direction
    "s4_vibe": [
        "Big & festive",
        "Intimate",
        "Family-led",
        "Still figuring out",
        "Whimsical & playful",
        "Modern & sleek",
        "Traditional & rooted",
        "Bohemian",
        "Royal & grand",
        "Warm & personal",
        "Minimalist",
        "Maximalist",
        "Relaxed & easy",
        "Dramatic & theatrical",
    ],

    # S7 — Events / Functions
    "s7_events": [
        "Mehndi",
        "Haldi",
        "Sangeet",
        "Wedding Ceremony",
        "Reception",
        "Engagement",
        "Ring Ceremony",
        "Tilak / Roka",
        "Cocktail Night",
        "After Party",
        "Rehearsal Dinner",
        "Welcome Dinner",
        "Bidaai",
    ],
}


def get_chip_pool(stage: str) -> list[str]:
    """Return the chip pool for a given stage id, or empty list if none defined."""
    return CHIP_POOLS.get(stage, [])


def format_chip_pool_for_prompt(stage: str) -> str:
    """Format the chip pool as a comma-separated string for prompt injection."""
    pool = get_chip_pool(stage)
    if not pool:
        return ""
    return ", ".join(f'"{c}"' for c in pool)
