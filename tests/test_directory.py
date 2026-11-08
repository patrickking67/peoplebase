import csv
import io

import pytest


def row_count(response):
    return response.data.count(b'class="row-actions"')


def read_csv(response):
    return list(csv.reader(io.StringIO(response.get_data(as_text=True))))


@pytest.fixture()
def bulk_staff(add_employees):
    """Thirty analysts at 1,000 each, on top of the seeded engineer."""
    add_employees(*[(f"Person {i:02d}", "Analyst", 1000) for i in range(1, 31)])


# Search and filters


def test_directory_lists_employees(client):
    response = client.get("/employees")

    assert response.status_code == 200
    assert b"Avery Example" in response.data
    assert b"Engineer" in response.data


def test_search_matches_name_case_insensitively(client):
    assert b"Avery Example" in client.get("/employees?q=AVERY").data


def test_search_matches_position(client):
    assert b"Avery Example" in client.get("/employees?q=engin").data


def test_search_without_matches_shows_empty_state(client):
    response = client.get("/employees?q=designer")

    assert response.status_code == 200
    assert b"No records found" in response.data


@pytest.mark.parametrize("term", ["%", "_", "%%", "Avery%Example"])
def test_search_treats_like_wildcards_literally(client, term):
    response = client.get("/employees", query_string={"q": term})

    assert b"No records found" in response.data


def test_position_filter_is_exact(client, add_employees):
    add_employees(("Jordan Sample", "Engineering Manager", 150000))

    response = client.get("/employees?position=Engineer")

    assert b"Avery Example" in response.data
    assert b"Jordan Sample" not in response.data


def test_search_and_position_filter_combine(client, add_employees):
    add_employees(("Jordan Sample", "Engineer", 110000), ("Casey Placeholder", "Designer", 90000))

    response = client.get("/employees?q=jordan&position=Engineer")

    assert b"Jordan Sample" in response.data
    assert b"Avery Example" not in response.data
    assert b"Casey Placeholder" not in response.data


# Reporting


def test_summary_reports_count_payroll_and_average(client, add_employees):
    add_employees(("Jordan Sample", "Designer", 90000), ("Casey Placeholder", "Designer", 60000))

    response = client.get("/employees?position=Designer")

    assert b"<strong>2</strong><span>Matching records</span>" in response.data
    assert b"$150,000</strong><span>Annual payroll" in response.data
    assert b"$75,000</strong><span>Average salary" in response.data


def test_summary_covers_every_page_not_just_the_current_one(client, bulk_staff):
    response = client.get("/employees?per_page=10")

    assert row_count(response) == 10
    assert b"<strong>31</strong><span>Matching records</span>" in response.data
    assert b"$155,000</strong><span>Annual payroll" in response.data
    assert b"$5,000</strong><span>Average salary" in response.data


def test_summary_is_zero_when_nothing_matches(client):
    response = client.get("/employees?q=nobody")

    assert b"<strong>0</strong><span>Matching records</span>" in response.data
    assert b"$0</strong><span>Annual payroll" in response.data


# Pagination


def test_directory_paginates_25_per_page_by_default(client, bulk_staff):
    first = client.get("/employees")
    second = client.get("/employees?page=2")

    assert row_count(first) == 25
    assert b"Page 1 of 2" in first.data
    assert b'rel="next"' in first.data
    assert row_count(second) == 6
    assert b'rel="prev"' in second.data


def test_short_result_sets_have_no_pagination(client):
    assert b'class="pagination"' not in client.get("/employees").data


@pytest.mark.parametrize("page", ["0", "-3", "abc"])
def test_invalid_page_falls_back_to_first_page(client, bulk_staff, page):
    response = client.get(f"/employees?page={page}")

    assert response.status_code == 200
    assert b"Page 1 of 2" in response.data


def test_page_past_the_end_shows_last_page(client, bulk_staff):
    response = client.get("/employees?page=999")

    assert response.status_code == 200
    assert b"Page 2 of 2" in response.data
    assert row_count(response) == 6


def test_per_page_is_capped(client, add_employees):
    add_employees(*[(f"Person {i:03d}", "Analyst", 1000) for i in range(1, 150)])

    assert row_count(client.get("/employees?per_page=1000")) == 100
    assert row_count(client.get("/employees?per_page=0")) == 1


def test_pagination_links_keep_filters(client, bulk_staff):
    response = client.get("/employees?q=person&position=Analyst&per_page=5")

    next_link = response.data.split(b'rel="next"')[0].rsplit(b'href="', 1)[1]
    assert b"page=2" in next_link
    assert b"q=person" in next_link
    assert b"position=Analyst" in next_link
    assert b"per_page=5" in next_link


# CSV export


def test_csv_export_headers_and_rows(client):
    response = client.get("/employees.csv")

    assert response.status_code == 200
    assert response.mimetype == "text/csv"
    assert "peoplebase-employees.csv" in response.headers["Content-Disposition"]
    assert read_csv(response) == [
        ["id", "name", "position", "annual_salary"],
        ["1", "Avery Example", "Engineer", "125000.00"],
    ]


def test_csv_export_uses_current_filters(client, add_employees):
    add_employees(("Jordan Sample", "Designer", 90000))

    rows = read_csv(client.get("/employees.csv?position=Designer"))

    assert [row[1] for row in rows[1:]] == ["Jordan Sample"]


def test_csv_export_includes_every_match_regardless_of_page(client, bulk_staff):
    rows = read_csv(client.get("/employees.csv?page=2&per_page=5"))

    assert len(rows) == 1 + 31


def test_directory_links_to_filtered_export(client):
    response = client.get("/employees?q=avery")

    assert b"/employees.csv?q=avery" in response.data


@pytest.mark.parametrize("page", ["99999999999999999999", str(2**63)])
def test_huge_page_number_shows_last_page(client, bulk_staff, page):
    response = client.get(f"/employees?page={page}")

    assert response.status_code == 200
    assert b"Page 2 of 2" in response.data
    assert row_count(response) == 6


def test_huge_per_page_is_capped(client, bulk_staff):
    response = client.get("/employees?per_page=99999999999999999999")

    assert response.status_code == 200
    assert row_count(response) == 31
