from score_jobs import format_resume_to_text


class TestFormatResume:
    def test_empty_resume(self):
        result = format_resume_to_text(None)
        assert result == "Resume data is not available."

        result = format_resume_to_text({})
        assert result == "Resume data is not available."

    def test_basic_info(self):
        resume = {
            "name": "Alice",
            "email": "alice@test.com",
            "phone": "123-456-7890",
            "location": "Berlin",
        }
        result = format_resume_to_text(resume)
        assert "Name: Alice" in result
        assert "Email: alice@test.com" in result
        assert "Phone: 123-456-7890" in result
        assert "Location: Berlin" in result

    def test_skills(self):
        resume = {"skills": ["Python", "Docker", "Kubernetes"]}
        result = format_resume_to_text(resume)
        assert "Python" in result
        assert "Docker" in result
        assert "Kubernetes" in result

    def test_experience(self):
        resume = {
            "experience": [{
                "job_title": "Engineer",
                "company": "TechCo",
                "start_date": "2020",
                "end_date": "2023",
                "description": "Built stuff",
            }]
        }
        result = format_resume_to_text(resume)
        assert "Engineer" in result
        assert "TechCo" in result
        assert "2020" in result
        assert "2023" in result

    def test_education(self):
        resume = {
            "education": [{
                "degree": "MSc",
                "field_of_study": "CS",
                "institution": "Uni",
                "start_year": "2018",
                "end_year": "2020",
            }]
        }
        result = format_resume_to_text(resume)
        assert "MSc" in result
        assert "CS" in result
        assert "Uni" in result

    def test_projects(self):
        resume = {
            "projects": [{
                "name": "MyApp",
                "description": "Cool app",
                "technologies": ["React", "Node"],
            }]
        }
        result = format_resume_to_text(resume)
        assert "MyApp" in result
        assert "Cool app" in result
        assert "React" in result
        assert "Node" in result

    def test_certifications(self):
        resume = {
            "certifications": [{
                "name": "AWS Certified",
                "issuer": "Amazon",
                "year": "2023",
            }]
        }
        result = format_resume_to_text(resume)
        assert "AWS Certified" in result
        assert "Amazon" in result
        assert "2023" in result

    def test_languages(self):
        resume = {"languages": ["English", "German"]}
        result = format_resume_to_text(resume)
        assert "English" in result
        assert "German" in result
