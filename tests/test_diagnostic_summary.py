"""
tests/test_diagnostic_summary.py
TASK-229: Unit tests for format_diagnostic_summary -- compact diagnostic
data extraction for LLM context injection.
"""
import pytest

from ai.tool_recovery import format_diagnostic_summary, _format_body_entry


# ---------------------------------------------------------------------------
# _format_body_entry
# ---------------------------------------------------------------------------

class TestFormatBodyEntry:

    def test_full_body_with_volume_and_bbox(self):
        body = {
            "name": "Box",
            "volume": 706.3,
            "boundingBox": {
                "min": {"x": 0, "y": 0, "z": 0},
                "max": {"x": 20, "y": 12, "z": 13},
            },
        }
        result = _format_body_entry(body)
        assert "Box" in result
        assert "706.3cm3" in result
        assert "0,0,0" in result
        assert "20,12,13" in result

    def test_body_with_snake_case_bbox(self):
        body = {
            "name": "Tube",
            "volume": 14.1,
            "bounding_box": {
                "min_point": {"x": 9.8, "y": 0, "z": 0.9},
                "max_point": {"x": 12.6, "y": 8.3, "z": 3.7},
            },
        }
        result = _format_body_entry(body)
        assert "Tube" in result
        assert "14.1cm3" in result
        assert "9.8,0,0.9" in result
        assert "12.6,8.3,3.7" in result

    def test_body_without_volume(self):
        body = {
            "name": "Panel",
            "boundingBox": {
                "min": {"x": 0, "y": 12, "z": 0},
                "max": {"x": 20, "y": 12.5, "z": 13},
            },
        }
        result = _format_body_entry(body)
        assert "Panel" in result
        assert "cm3" not in result
        assert "0,12,0" in result

    def test_body_without_bbox(self):
        body = {"name": "Sphere", "volume": 523.6}
        result = _format_body_entry(body)
        assert "Sphere" in result
        assert "523.6cm3" in result
        assert "to" not in result

    def test_body_with_neither_volume_nor_bbox(self):
        body = {"name": "EmptyBody"}
        result = _format_body_entry(body)
        assert result == "EmptyBody"

    def test_body_missing_name(self):
        body = {"volume": 10.0}
        result = _format_body_entry(body)
        assert "unnamed" in result
        assert "10.0cm3" in result

    def test_volume_none_is_skipped(self):
        body = {"name": "Test", "volume": None}
        result = _format_body_entry(body)
        assert "cm3" not in result

    def test_invalid_volume_is_skipped(self):
        body = {"name": "Test", "volume": "not_a_number"}
        result = _format_body_entry(body)
        assert "cm3" not in result

    def test_empty_bbox_points_skipped(self):
        body = {
            "name": "Test",
            "boundingBox": {"min": {}, "max": {}},
        }
        result = _format_body_entry(body)
        # Empty dicts still have x/y/z default to 0
        assert "Test" in result

    def test_bbox_with_minPoint_maxPoint_keys(self):
        body = {
            "name": "Alt",
            "boundingBox": {
                "minPoint": {"x": 1, "y": 2, "z": 3},
                "maxPoint": {"x": 4, "y": 5, "z": 6},
            },
        }
        result = _format_body_entry(body)
        assert "1,2,3" in result
        assert "4,5,6" in result

    def test_rounding_precision(self):
        body = {
            "name": "Precise",
            "volume": 123.456789,
            "boundingBox": {
                "min": {"x": 0.123, "y": 4.567, "z": 8.999},
                "max": {"x": 10.001, "y": 20.555, "z": 30.0},
            },
        }
        result = _format_body_entry(body)
        assert "123.5cm3" in result
        assert "0.1,4.6,9.0" in result
        assert "10.0,20.6,30.0" in result


# ---------------------------------------------------------------------------
# format_diagnostic_summary -- body_list
# ---------------------------------------------------------------------------

class TestDiagnosticSummaryBodyList:

    def test_basic_body_list(self):
        diag = {
            "body_list": {
                "bodies": [
                    {
                        "name": "Standoff1",
                        "volume": 706.3,
                        "boundingBox": {
                            "min": {"x": 0, "y": 0, "z": 0},
                            "max": {"x": 20, "y": 12, "z": 13},
                        },
                    },
                    {
                        "name": "Port_Tube",
                        "volume": 14.1,
                        "boundingBox": {
                            "min": {"x": 9.8, "y": 0, "z": 0.9},
                            "max": {"x": 12.6, "y": 8.3, "z": 3.7},
                        },
                    },
                ],
            },
        }
        result = format_diagnostic_summary(diag)
        assert result.startswith("[DESIGN STATE]")
        assert "2 bodies" in result
        assert "Standoff1" in result
        assert "Port_Tube" in result
        assert "706.3cm3" in result
        assert "14.1cm3" in result

    def test_empty_body_list(self):
        diag = {"body_list": {"bodies": [], "count": 0}}
        result = format_diagnostic_summary(diag)
        assert "0 bodies" in result
        assert "empty design" in result

    def test_body_list_with_no_bodies_key(self):
        diag = {"body_list": {"count": 0}}
        result = format_diagnostic_summary(diag)
        assert "0 bodies" in result

    def test_body_list_not_a_dict(self):
        diag = {"body_list": "not a dict"}
        result = format_diagnostic_summary(diag)
        assert result == ""

    def test_single_body(self):
        diag = {
            "body_list": {
                "bodies": [
                    {"name": "Solo", "volume": 100.0},
                ],
            },
        }
        result = format_diagnostic_summary(diag)
        assert "1 bodies" in result
        assert "Solo" in result


# ---------------------------------------------------------------------------
# format_diagnostic_summary -- sketch_info
# ---------------------------------------------------------------------------

class TestDiagnosticSummarySketchInfo:

    def test_sketch_info(self):
        diag = {
            "sketch_info": {
                "name": "Sketch1",
                "profile_count": 2,
                "curve_count": 8,
            },
        }
        result = format_diagnostic_summary(diag)
        assert "[DESIGN STATE]" in result
        assert "sketch 'Sketch1'" in result
        assert "2 profiles" in result
        assert "8 curves" in result

    def test_sketch_info_partial(self):
        diag = {
            "sketch_info": {
                "name": "S2",
                "profile_count": 0,
            },
        }
        result = format_diagnostic_summary(diag)
        assert "sketch 'S2'" in result
        assert "0 profiles" in result
        assert "curves" not in result

    def test_sketch_info_not_dict(self):
        diag = {"sketch_info": "invalid"}
        result = format_diagnostic_summary(diag)
        assert result == ""


# ---------------------------------------------------------------------------
# format_diagnostic_summary -- body_properties
# ---------------------------------------------------------------------------

class TestDiagnosticSummaryBodyProperties:

    def test_body_properties(self):
        diag = {
            "body_properties": {
                "name": "MainBody",
                "volume": 500.5,
                "area": 1200.3,
                "face_count": 12,
            },
        }
        result = format_diagnostic_summary(diag)
        assert "[DESIGN STATE]" in result
        assert "body 'MainBody'" in result
        assert "vol=500.5cm3" in result
        assert "area=1200.3cm2" in result
        assert "12 faces" in result

    def test_body_properties_partial(self):
        diag = {
            "body_properties": {
                "name": "Simple",
                "face_count": 6,
            },
        }
        result = format_diagnostic_summary(diag)
        assert "body 'Simple'" in result
        assert "6 faces" in result
        assert "vol=" not in result


# ---------------------------------------------------------------------------
# format_diagnostic_summary -- combined
# ---------------------------------------------------------------------------

class TestDiagnosticSummaryCombined:

    def test_body_list_and_sketch_info(self):
        diag = {
            "body_list": {
                "bodies": [
                    {"name": "Box", "volume": 100},
                ],
            },
            "sketch_info": {
                "name": "Sketch1",
                "profile_count": 1,
            },
        }
        result = format_diagnostic_summary(diag)
        assert "1 bodies" in result
        assert "sketch 'Sketch1'" in result
        # Uses semicolons to separate sections
        assert ";" in result

    def test_all_three_sections(self):
        diag = {
            "body_list": {
                "bodies": [{"name": "A", "volume": 10}],
            },
            "sketch_info": {
                "name": "S1",
                "profile_count": 2,
            },
            "body_properties": {
                "name": "A",
                "volume": 10,
                "face_count": 6,
            },
        }
        result = format_diagnostic_summary(diag)
        assert "1 bodies" in result
        assert "sketch 'S1'" in result
        assert "body 'A'" in result


# ---------------------------------------------------------------------------
# format_diagnostic_summary -- edge cases
# ---------------------------------------------------------------------------

class TestDiagnosticSummaryEdgeCases:

    def test_empty_dict(self):
        assert format_diagnostic_summary({}) == ""

    def test_none_input(self):
        assert format_diagnostic_summary(None) == ""

    def test_non_dict_input(self):
        assert format_diagnostic_summary("string") == ""
        assert format_diagnostic_summary(42) == ""
        assert format_diagnostic_summary([]) == ""

    def test_unknown_keys_ignored(self):
        diag = {"unknown_field": {"some": "data"}}
        assert format_diagnostic_summary(diag) == ""

    def test_bodies_with_non_dict_entries(self):
        diag = {
            "body_list": {
                "bodies": [
                    {"name": "Good", "volume": 10},
                    "not_a_dict",
                    None,
                    {"name": "Also_Good", "volume": 20},
                ],
            },
        }
        result = format_diagnostic_summary(diag)
        assert "Good" in result
        assert "Also_Good" in result
        # The non-dict entries are skipped; count still uses len(bodies)
        assert "4 bodies" in result
