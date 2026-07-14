"""Vendor seed data — real-looking Indian wedding vendors."""

VENDORS: list[dict] = [
    # ── VENUES ─────────────────────────────────────────────────────────────────
    {
        "slug": "the-leela-ambience-delhi",
        "name": "The Leela Ambience Gurugram",
        "vendor_type": "venue",
        "primary_city": "Delhi",
        "primary_region": "Delhi NCR",
        "short_description": "Five-star luxury venue with grand ballrooms and rooftop terraces in Gurugram.",
        "profile_json": {
            "contact": {"name": "Events Team", "email": "events@leela-gurugram.in", "phone": "+91-124-477-1234", "websiteUrl": "https://theleela.com", "instagramHandle": "@theleelacollection"},
            "services": [{"serviceId": "v1", "category": "venue", "serviceName": "Ballroom & Terrace Package", "description": "Grand ballroom for 500+ guests, rooftop terrace for 200.", "pricingModel": "per_event", "startingPriceAmount": 1500000, "maxPriceAmount": 5000000, "currencyCode": "INR", "serviceAreas": ["Delhi NCR", "Gurugram"], "packages": ["Full Day", "Evening Only"], "styleTags": ["luxury", "grand", "corporate-capable"], "occasionTags": ["reception", "sangeet", "wedding ceremony"], "capacityNotes": "Ballroom: 800 seated, Terrace: 250 cocktail"}],
            "portfolio": [],
            "availability": {"status": "active", "notes": "Book 6-12 months in advance for winter season"},
            "businessMeta": {"languages": ["Hindi", "English"], "travelPolicy": "Delhi NCR only", "preferredClientTypes": ["luxury", "corporate", "destination"]}
        },
        "rating_summary_json": {"reviewCount": 142, "averageRating": 4.7, "sources": ["WeddingWire", "JustDial"], "highlights": ["impeccable service", "grand interiors", "excellent catering"], "lastReviewedAt": "2026-03-15"},
        "is_preferred": True, "is_active": True, "seed_version": "v1",
    },
    {
        "slug": "rambagh-palace-jaipur",
        "name": "Rambagh Palace Jaipur",
        "vendor_type": "venue",
        "primary_city": "Jaipur",
        "primary_region": "Rajasthan",
        "short_description": "Once home to the Maharaja of Jaipur — a palace-hotel with lawns, courtyards, and heritage grandeur.",
        "profile_json": {
            "contact": {"name": "Wedding Concierge", "email": "weddings@rambaghpalace.com", "phone": "+91-141-221-1919", "websiteUrl": "https://tajhotels.com/rambagh", "instagramHandle": "@rambaghpalace"},
            "services": [{"serviceId": "v1", "category": "venue", "serviceName": "Heritage Palace Package", "description": "Palace lawns, Zenana courtyard, and indoor royal suites.", "pricingModel": "per_event", "startingPriceAmount": 3000000, "maxPriceAmount": 15000000, "currencyCode": "INR", "serviceAreas": ["Jaipur", "Rajasthan"], "packages": ["Exclusive Buyout", "Lawn Only", "Full Palace"], "styleTags": ["regal", "heritage", "Rajput"], "occasionTags": ["wedding ceremony", "reception", "mehendi", "sangeet"], "capacityNotes": "Lawns: 1500 guests, Courtyard: 400, Indoor: 200"}],
            "portfolio": [],
            "availability": {"status": "active", "notes": "Winter season books 12-18 months ahead"},
            "businessMeta": {"languages": ["Hindi", "English"], "travelPolicy": "Rajasthan", "preferredClientTypes": ["luxury", "destination", "heritage"]}
        },
        "rating_summary_json": {"reviewCount": 89, "averageRating": 4.9, "sources": ["WeddingWire", "Vogue India"], "highlights": ["unmatched heritage", "royal experience", "world-class staff"], "lastReviewedAt": "2026-02-20"},
        "is_preferred": True, "is_active": True, "seed_version": "v1",
    },
    {
        "slug": "taj-lake-palace-udaipur",
        "name": "Taj Lake Palace Udaipur",
        "vendor_type": "venue",
        "primary_city": "Udaipur",
        "primary_region": "Rajasthan",
        "short_description": "Floating palace on Lake Pichola — one of the most iconic wedding venues in the world.",
        "profile_json": {
            "contact": {"name": "Events", "email": "tlpu.weddings@tajhotels.com", "phone": "+91-294-242-8800", "websiteUrl": "https://tajhotels.com/lake-palace", "instagramHandle": "@tajlakepalace"},
            "services": [{"serviceId": "v1", "category": "venue", "serviceName": "Lake Palace Exclusive", "description": "Full palace on water — courtyard ceremonies, rooftop receptions, and lake-view dinners.", "pricingModel": "per_event", "startingPriceAmount": 5000000, "maxPriceAmount": 25000000, "currencyCode": "INR", "serviceAreas": ["Udaipur"], "packages": ["Exclusive Buyout"], "styleTags": ["destination", "palace", "waterfront", "iconic"], "occasionTags": ["wedding ceremony", "reception", "mehendi"], "capacityNotes": "Up to 250 guests exclusive"}],
            "portfolio": [],
            "availability": {"status": "active", "notes": "Extremely limited — book 18+ months ahead"},
            "businessMeta": {"languages": ["Hindi", "English"], "travelPolicy": "Udaipur", "preferredClientTypes": ["ultra-luxury", "destination", "international"]}
        },
        "rating_summary_json": {"reviewCount": 61, "averageRating": 5.0, "sources": ["Condé Nast Traveller", "Vogue"], "highlights": ["bucket list venue", "flawless execution", "incomparable setting"], "lastReviewedAt": "2026-01-10"},
        "is_preferred": True, "is_active": True, "seed_version": "v1",
    },
    # ── DECOR ──────────────────────────────────────────────────────────────────
    {
        "slug": "devika-narain-studio",
        "name": "Devika Narain & Co.",
        "vendor_type": "decor",
        "primary_city": "Delhi",
        "primary_region": "Delhi NCR",
        "short_description": "Premium floral and décor studio known for editorial-quality design and restrained luxury.",
        "profile_json": {
            "contact": {"name": "Studio Team", "email": "hello@devikanarain.com", "phone": "+91-98100-00001", "websiteUrl": "https://devikanarain.com", "instagramHandle": "@devikanarainstudio"},
            "services": [{"serviceId": "v1", "category": "decor", "serviceName": "Full Wedding Décor", "description": "Concept to execution — florals, draping, lighting, and set design.", "pricingModel": "custom_quote", "startingPriceAmount": 500000, "maxPriceAmount": 8000000, "currencyCode": "INR", "serviceAreas": ["Pan India", "International"], "packages": ["Consultation Only", "Full Service"], "styleTags": ["editorial", "minimal luxury", "floral-heavy", "intimate"], "occasionTags": ["all events"], "capacityNotes": "Takes limited projects per season"}],
            "portfolio": [],
            "availability": {"status": "active", "notes": "Book 9-12 months ahead for winter"},
            "businessMeta": {"languages": ["English", "Hindi"], "travelPolicy": "Pan India + international at cost", "preferredClientTypes": ["design-conscious", "intimate weddings", "editorial"]}
        },
        "rating_summary_json": {"reviewCount": 78, "averageRating": 4.8, "sources": ["Vogue India", "Brides Today"], "highlights": ["stunning florals", "original concepts", "detail-obsessed"], "lastReviewedAt": "2026-03-01"},
        "is_preferred": True, "is_active": True, "seed_version": "v1",
    },
    {
        "slug": "shaadi-squad-decor",
        "name": "Shaadi Squad Décor",
        "vendor_type": "decor",
        "primary_city": "Mumbai",
        "primary_region": "Maharashtra",
        "short_description": "Mumbai-based full-service décor team specialising in grand Indian weddings and sangeets.",
        "profile_json": {
            "contact": {"name": "Rajan Mehta", "email": "rajan@shaadisquad.in", "phone": "+91-98200-00002", "websiteUrl": "", "instagramHandle": "@shaadisquaddecor"},
            "services": [{"serviceId": "v1", "category": "decor", "serviceName": "Grand Wedding Package", "description": "All-inclusive floral, draping, stage, and lighting for all functions.", "pricingModel": "per_event", "startingPriceAmount": 400000, "maxPriceAmount": 3000000, "currencyCode": "INR", "serviceAreas": ["Mumbai", "Pune", "Goa"], "packages": ["Economy", "Premium", "Grand"], "styleTags": ["grand", "festive", "Bollywood-inspired"], "occasionTags": ["mehendi", "sangeet", "reception"], "capacityNotes": "Handles 200-2000 guest events"}],
            "portfolio": [],
            "availability": {"status": "active", "notes": ""},
            "businessMeta": {"languages": ["Hindi", "Marathi", "English"], "travelPolicy": "Mumbai, Pune, Goa", "preferredClientTypes": ["large families", "festive weddings"]}
        },
        "rating_summary_json": {"reviewCount": 212, "averageRating": 4.5, "sources": ["WedMeGood", "JustDial"], "highlights": ["great value", "reliable team", "beautiful stage work"], "lastReviewedAt": "2026-04-10"},
        "is_preferred": False, "is_active": True, "seed_version": "v1",
    },
    # ── CATERING ───────────────────────────────────────────────────────────────
    {
        "slug": "the-great-kabab-factory-catering",
        "name": "The Great Kabab Factory Catering",
        "vendor_type": "catering",
        "primary_city": "Delhi",
        "primary_region": "Delhi NCR",
        "short_description": "Premium North Indian catering — live kabab stations, royal daawat menus, and Mughlai spreads.",
        "profile_json": {
            "contact": {"name": "Catering Head", "email": "catering@gkf.in", "phone": "+91-11-4000-0001", "websiteUrl": "", "instagramHandle": ""},
            "services": [{"serviceId": "v1", "category": "catering", "serviceName": "Royal Daawat Menu", "description": "Mughlai and North Indian cuisine with live grill stations and dessert counters.", "pricingModel": "per_plate", "startingPriceAmount": 1800, "maxPriceAmount": 4500, "currencyCode": "INR", "serviceAreas": ["Delhi NCR"], "packages": ["Veg", "Non-Veg", "Premium Combo"], "styleTags": ["North Indian", "Mughlai", "live stations"], "occasionTags": ["reception", "baraat dinner", "sangeet"], "capacityNotes": "Minimum 200 covers"}],
            "portfolio": [],
            "availability": {"status": "active", "notes": ""},
            "businessMeta": {"languages": ["Hindi", "English"], "travelPolicy": "Delhi NCR only", "preferredClientTypes": ["large events", "North Indian families"]}
        },
        "rating_summary_json": {"reviewCount": 340, "averageRating": 4.6, "sources": ["Zomato Events", "WedMeGood"], "highlights": ["delicious kababs", "live stations a hit", "professional staff"], "lastReviewedAt": "2026-05-01"},
        "is_preferred": True, "is_active": True, "seed_version": "v1",
    },
    {
        "slug": "mumbai-mahal-catering",
        "name": "Mumbai Mahal Caterers",
        "vendor_type": "catering",
        "primary_city": "Mumbai",
        "primary_region": "Maharashtra",
        "short_description": "Multi-cuisine wedding caterers serving Mumbai for 30+ years. Gujarati, Maharashtrian, and continental spreads.",
        "profile_json": {
            "contact": {"name": "Dinesh Patel", "email": "dinesh@mumbaimahal.com", "phone": "+91-98200-00003", "websiteUrl": "", "instagramHandle": ""},
            "services": [{"serviceId": "v1", "category": "catering", "serviceName": "Multi-Cuisine Wedding Package", "description": "Gujarati thali, Maharashtrian spread, south Indian counter, and live pasta station.", "pricingModel": "per_plate", "startingPriceAmount": 1200, "maxPriceAmount": 3200, "currencyCode": "INR", "serviceAreas": ["Mumbai", "Navi Mumbai", "Thane"], "packages": ["Pure Veg", "Mixed", "Premium"], "styleTags": ["multi-cuisine", "Gujarati", "Maharashtrian"], "occasionTags": ["all events"], "capacityNotes": "50-3000 covers"}],
            "portfolio": [],
            "availability": {"status": "active", "notes": ""},
            "businessMeta": {"languages": ["Gujarati", "Hindi", "Marathi", "English"], "travelPolicy": "Greater Mumbai", "preferredClientTypes": ["Gujarati families", "large events"]}
        },
        "rating_summary_json": {"reviewCount": 480, "averageRating": 4.4, "sources": ["JustDial", "WedMeGood"], "highlights": ["reliable", "good Gujarati thali", "on time"], "lastReviewedAt": "2026-04-20"},
        "is_preferred": False, "is_active": True, "seed_version": "v1",
    },
    # ── PHOTOGRAPHY ────────────────────────────────────────────────────────────
    {
        "slug": "joseph-radhik-photography",
        "name": "Joseph Radhik",
        "vendor_type": "photography",
        "primary_city": "Mumbai",
        "primary_region": "Maharashtra",
        "short_description": "India's most celebrated wedding photographer. Editorial storytelling and cinematic film-style images.",
        "profile_json": {
            "contact": {"name": "Studio", "email": "hello@josephradhik.com", "phone": "+91-98200-00004", "websiteUrl": "https://josephradhik.com", "instagramHandle": "@josephradhik"},
            "services": [
                {"serviceId": "v1", "category": "photography", "serviceName": "Full Wedding Coverage", "description": "3-5 day coverage with a team of 4 photographers and 2 videographers.", "pricingModel": "custom_quote", "startingPriceAmount": 800000, "maxPriceAmount": 5000000, "currencyCode": "INR", "serviceAreas": ["Pan India", "International"], "packages": ["3-Day", "5-Day", "Custom"], "styleTags": ["editorial", "cinematic", "documentary"], "occasionTags": ["all functions"], "capacityNotes": "Very selective — 25-30 weddings per year"}
            ],
            "portfolio": [],
            "availability": {"status": "active", "notes": "Book 12-18 months ahead. Very limited slots."},
            "businessMeta": {"languages": ["English", "Tamil", "Hindi"], "travelPolicy": "Worldwide", "preferredClientTypes": ["premium", "editorial", "destination"]}
        },
        "rating_summary_json": {"reviewCount": 45, "averageRating": 5.0, "sources": ["Vogue India", "Harper's Bazaar", "Architectural Digest India"], "highlights": ["once in a lifetime work", "invisible on the day", "art-level output"], "lastReviewedAt": "2026-02-14"},
        "is_preferred": True, "is_active": True, "seed_version": "v1",
    },
    {
        "slug": "picture-mango-photography",
        "name": "Picture Mango",
        "vendor_type": "photography",
        "primary_city": "Delhi",
        "primary_region": "Delhi NCR",
        "short_description": "Warm and candid wedding photography studio based in Delhi. Known for emotional storytelling.",
        "profile_json": {
            "contact": {"name": "Arjun Kapur", "email": "arjun@picturemango.com", "phone": "+91-98100-00005", "websiteUrl": "https://picturemango.com", "instagramHandle": "@picturemango"},
            "services": [{"serviceId": "v1", "category": "photography", "serviceName": "Wedding Photo + Video", "description": "2-3 day coverage, 2 photographers, 1 cinematographer, edited album.", "pricingModel": "per_event", "startingPriceAmount": 150000, "maxPriceAmount": 600000, "currencyCode": "INR", "serviceAreas": ["Delhi NCR", "Rajasthan", "Pan India at cost"], "packages": ["1-Day", "2-Day", "Full Functions"], "styleTags": ["candid", "emotional", "warm", "natural light"], "occasionTags": ["all functions"], "capacityNotes": ""}],
            "portfolio": [],
            "availability": {"status": "active", "notes": "Book 4-6 months ahead for peak season"},
            "businessMeta": {"languages": ["Hindi", "English"], "travelPolicy": "Pan India + travel charges apply", "preferredClientTypes": ["candid lovers", "intimate weddings", "mid-range budget"]}
        },
        "rating_summary_json": {"reviewCount": 189, "averageRating": 4.7, "sources": ["WedMeGood", "WeddingWire"], "highlights": ["beautiful candids", "made us feel at ease", "delivered on time"], "lastReviewedAt": "2026-03-22"},
        "is_preferred": True, "is_active": True, "seed_version": "v1",
    },
    # ── ENTERTAINMENT ──────────────────────────────────────────────────────────
    {
        "slug": "dj-suketu-entertainment",
        "name": "DJ Suketu",
        "vendor_type": "entertainment",
        "primary_city": "Mumbai",
        "primary_region": "Maharashtra",
        "short_description": "One of India's top wedding DJs. Known for Bollywood, EDM, and seamless energy management.",
        "profile_json": {
            "contact": {"name": "Management", "email": "bookings@djsuketu.com", "phone": "+91-98200-00006", "websiteUrl": "https://djsuketu.com", "instagramHandle": "@djsuketu"},
            "services": [{"serviceId": "v1", "category": "entertainment", "serviceName": "Premium DJ Set", "description": "Full evening DJ performance with lighting rig and sound system.", "pricingModel": "per_event", "startingPriceAmount": 200000, "maxPriceAmount": 1000000, "currencyCode": "INR", "serviceAreas": ["Pan India"], "packages": ["3-Hour", "5-Hour", "Full Night"], "styleTags": ["Bollywood", "EDM", "Punjabi", "retro"], "occasionTags": ["sangeet", "reception", "after party"], "capacityNotes": "Any size event"}],
            "portfolio": [],
            "availability": {"status": "active", "notes": "Book 6-12 months ahead for peak season"},
            "businessMeta": {"languages": ["Hindi", "English"], "travelPolicy": "Pan India + travel charges", "preferredClientTypes": ["festive weddings", "dance-heavy sangeets"]}
        },
        "rating_summary_json": {"reviewCount": 310, "averageRating": 4.8, "sources": ["WedMeGood", "BookMyShow Events"], "highlights": ["best DJ in India", "crowd control genius", "energy never drops"], "lastReviewedAt": "2026-04-05"},
        "is_preferred": True, "is_active": True, "seed_version": "v1",
    },
    {
        "slug": "qawwali-ensemble-delhi",
        "name": "Ustaad Raza Qawwali Ensemble",
        "vendor_type": "entertainment",
        "primary_city": "Delhi",
        "primary_region": "Delhi NCR",
        "short_description": "Authentic Sufi qawwali performance by a 12-member ensemble. Deeply spiritual and mesmerising.",
        "profile_json": {
            "contact": {"name": "Ustaad Raza Ali", "email": "raza@qawwaliensemble.in", "phone": "+91-98100-00007", "websiteUrl": "", "instagramHandle": ""},
            "services": [{"serviceId": "v1", "category": "entertainment", "serviceName": "Qawwali Night Performance", "description": "90-min to 3-hour qawwali performance with harmonium, tabla, and chorus.", "pricingModel": "per_event", "startingPriceAmount": 80000, "maxPriceAmount": 300000, "currencyCode": "INR", "serviceAreas": ["Delhi NCR", "Pan India at cost"], "packages": ["90 min", "2 hours", "Full Night"], "styleTags": ["qawwali", "sufi", "classical", "spiritual"], "occasionTags": ["mehendi night", "sufi evening", "sangeet alternative"], "capacityNotes": "Any intimate to large setting"}],
            "portfolio": [],
            "availability": {"status": "active", "notes": ""},
            "businessMeta": {"languages": ["Urdu", "Hindi"], "travelPolicy": "Pan India + travel charges", "preferredClientTypes": ["music lovers", "sufi-themed events"]}
        },
        "rating_summary_json": {"reviewCount": 56, "averageRating": 4.9, "sources": ["Google", "WedMeGood"], "highlights": ["unforgettable evening", "deeply moving", "professional and punctual"], "lastReviewedAt": "2026-01-30"},
        "is_preferred": False, "is_active": True, "seed_version": "v1",
    },
    # ── PLANNER / COORDINATOR ──────────────────────────────────────────────────
    {
        "slug": "shaadi-squad-planning",
        "name": "The Wedding Design Co.",
        "vendor_type": "planner",
        "primary_city": "Delhi",
        "primary_region": "Delhi NCR",
        "short_description": "Boutique wedding planning studio managing premium multi-day weddings across India.",
        "profile_json": {
            "contact": {"name": "Priya Sethi", "email": "priya@weddingdesignco.in", "phone": "+91-98100-00008", "websiteUrl": "", "instagramHandle": "@theweddingdesignco"},
            "services": [{"serviceId": "v1", "category": "planner", "serviceName": "Full Wedding Management", "description": "End-to-end coordination for multi-day events — vendor sourcing, logistics, day-of management.", "pricingModel": "percentage_of_budget", "startingPriceAmount": 300000, "maxPriceAmount": 2000000, "currencyCode": "INR", "serviceAreas": ["Pan India", "Destination Weddings"], "packages": ["Day-of Coordination", "Partial Planning", "Full Planning"], "styleTags": ["luxury", "boutique", "detail-oriented"], "occasionTags": ["all functions"], "capacityNotes": "Takes 15-20 weddings per year"}],
            "portfolio": [],
            "availability": {"status": "active", "notes": "Book 9-12 months ahead"},
            "businessMeta": {"languages": ["Hindi", "English"], "travelPolicy": "Pan India", "preferredClientTypes": ["premium", "destination", "multi-day"]}
        },
        "rating_summary_json": {"reviewCount": 94, "averageRating": 4.8, "sources": ["WedMeGood", "Vogue India Weddings"], "highlights": ["stress-free experience", "exceptional vendor network", "flawless execution"], "lastReviewedAt": "2026-02-28"},
        "is_preferred": True, "is_active": True, "seed_version": "v1",
    },
]
