import json
import tempfile
import unittest
from pathlib import Path

from agents.entity_resolution_agent import EntityResolutionAgent
from agents.query_agent import QueryAgent
from agents.reporting_agent import ReportingAgent
from agents.verification_agent import VerificationAgent


class QueryAgentTests(unittest.TestCase):
    def test_parse_falls_back_and_adds_search_variations(self):
        agent = QueryAgent()
        agent.llm.chat_json = lambda *_: None

        parsed = agent.parse("Dentists in Austin")

        self.assertEqual(parsed["category"], "Dentists")
        self.assertEqual(parsed["location"], "Austin")
        self.assertIn("Dentists in Austin", parsed["search_variations"])
        self.assertTrue(any(q.startswith("site:yelp.com") for q in parsed["search_variations"]))


class EntityResolutionTests(unittest.TestCase):
    def test_clusters_profiles_with_shared_phone(self):
        resolver = EntityResolutionAgent()
        profiles = [
            {"business_name": "ABC Hair Studio", "phone": "(512) 555-1111", "address": "", "website": ""},
            {"business_name": "ABC Salon LLC", "phone": "512-555-1111", "address": "", "website": ""},
            {"business_name": "Different Dental", "phone": "512-555-2222", "address": "", "website": ""},
        ]

        clusters = resolver.cluster(profiles)

        self.assertEqual(len(clusters), 2)
        self.assertEqual(len(clusters[0]), 2)


class VerificationAgentTests(unittest.TestCase):
    def test_conflict_resolution_uses_source_trust_and_records_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            learning_store = Path(tmp) / "source_reliability.json"
            learning_store.write_text(json.dumps({
                "facebook.com": {"wins": 1, "losses": 9},
                "bbb.org": {"wins": 9, "losses": 1},
            }))
            verifier = VerificationAgent(str(learning_store))

            merged = verifier.verify_group([
                {
                    "business_name": "Lakeway Dental",
                    "phone": "(512) 555-1111",
                    "website": "https://lakewaydental.example",
                    "_raw_source": "https://www.bbb.org/profile/lakeway-dental",
                },
                {
                    "business_name": "Lakeway Dental",
                    "phone": "(512) 555-2222",
                    "website": "https://lakewaydental.example",
                    "_raw_source": "https://www.facebook.com/lakewaydental",
                },
            ])

        self.assertEqual(merged["phone"], "(512) 555-1111")
        self.assertEqual(merged["verification_status"]["phone"], "verified_with_conflict_resolution")
        self.assertIn("phone", merged["field_evidence"])
        self.assertGreater(merged["field_confidence"]["phone"], 0)


class ReportingAgentTests(unittest.TestCase):
    def test_generate_counts_manual_review_fields_without_crashing(self):
        report = ReportingAgent().generate(
            query="Dentists in Austin",
            category="Dentist",
            location="Austin",
            raw_candidate_count=3,
            raw_profile_count=2,
            verified_profiles=[
                {
                    "business_name": "A",
                    "phone": "Requires Manual Verification",
                    "address": "1 Main",
                    "email": "",
                    "verification_score": 75,
                }
            ],
            pages_crawled=2,
            websites_visited=2,
            search_variations=5,
            duplicates_merged=1,
            duration_seconds=65,
            cache_hits=4,
        )

        self.assertEqual(report["fields_requiring_manual_review"], 1)
        self.assertEqual(report["profiles_extracted"], 2)
        self.assertEqual(report["cache_hits"], 4)
        self.assertEqual(report["research_duration"], "1 Minutes 5 Seconds")


if __name__ == "__main__":
    unittest.main()
