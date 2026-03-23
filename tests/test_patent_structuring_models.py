from agents.common.patent_structuring.models import PatentDocument


def test_patent_document_normalizes_null_string_fields_to_empty_strings() -> None:
    patent = PatentDocument.model_validate(
        {
            "bibliographic_data": {
                "application_number": None,
                "application_date": None,
                "priority_date": None,
                "publication_number": None,
                "publication_date": None,
                "invention_title": None,
                "ipc_classifications": ["G01K 7/36"],
                "applicants": [{"name": "华中科技大学", "address": None}],
                "inventors": ["张三"],
                "agency": None,
                "abstract": None,
                "abstract_figure": None,
            },
            "claims": [
                {
                    "claim_id": None,
                    "claim_text": "一种装置。",
                    "claim_type": "independent",
                    "parent_claim_ids": [],
                }
            ],
            "description": {
                "technical_field": None,
                "background_art": None,
                "summary_of_invention": None,
                "technical_effect": None,
                "brief_description_of_drawings": None,
                "detailed_description": None,
            },
            "drawings": [
                {
                    "file_path": "images/figure1.jpg",
                    "figure_label": "图1",
                    "caption": None,
                }
            ],
        }
    )

    assert patent.bibliographic_data.application_number == ""
    assert patent.bibliographic_data.priority_date == ""
    assert patent.bibliographic_data.abstract_figure == ""
    assert patent.bibliographic_data.applicants[0].address == ""
    assert patent.claims[0].claim_id == ""
    assert patent.description.technical_effect == ""
    assert patent.description.brief_description_of_drawings == ""
    assert patent.drawings[0].caption == ""


def test_patent_document_normalizes_date_fields_to_yyyy_mm_dd() -> None:
    patent = PatentDocument.model_validate(
        {
            "bibliographic_data": {
                "application_number": "A",
                "application_date": "Dec. 14, 2023",
                "priority_date": "14.06.2022",
                "publication_number": "B",
                "publication_date": "(2020.6.4)",
                "invention_title": "Title",
                "ipc_classifications": ["G01K 7/36"],
                "applicants": [{"name": "华中科技大学", "address": ""}],
                "inventors": ["张三"],
                "agency": None,
                "abstract": "摘要",
                "abstract_figure": "",
            },
            "claims": [
                {
                    "claim_id": "1",
                    "claim_text": "一种装置。",
                    "claim_type": "independent",
                    "parent_claim_ids": [],
                }
            ],
            "description": {
                "technical_field": "",
                "background_art": "",
                "summary_of_invention": "",
                "technical_effect": "",
                "brief_description_of_drawings": "",
                "detailed_description": "",
            },
            "drawings": [],
        }
    )

    assert patent.bibliographic_data.application_date == "2023.12.14"
    assert patent.bibliographic_data.priority_date == "2022.06.14"
    assert patent.bibliographic_data.publication_date == "2020.06.04"
