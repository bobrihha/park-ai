"""
Tests for Birthday Flow State Machine.

Run with: python3 -m pytest tests/test_birthday_flow.py -v
"""

import pytest
from core.birthday.state_machine import BirthdayState, BirthdayStateMachine
from core.birthday.messages import contains_birthday_trigger, BIRTHDAY_TRIGGERS


class TestBirthdayState:
    """Tests for BirthdayState enum."""
    
    def test_all_states_exist(self):
        """Verify all required states are defined."""
        required_states = [
            "IDLE", "CHECK_BOOKING", "EXISTING_BOOKING",
            "ASK_DATE", "ASK_KIDS", "CONFIRM_PHONE", "ASK_PHONE",
            "ASK_FORMAT", "ASK_TIME", "ASK_EXTRAS", "COMPLETE"
        ]
        for state_name in required_states:
            assert hasattr(BirthdayState, state_name), f"Missing state: {state_name}"
    
    def test_state_values(self):
        """Check state values are lowercase strings."""
        assert BirthdayState.IDLE.value == "idle"
        assert BirthdayState.ASK_DATE.value == "ask_date"
        assert BirthdayState.COMPLETE.value == "complete"


class TestBirthdayStateMachine:
    """Tests for BirthdayStateMachine transitions."""
    
    def test_valid_transitions(self):
        """Verify valid transition paths."""
        sm = BirthdayStateMachine("test_user_123", "telegram")
        
        # Check transition validity
        assert sm.can_transition(BirthdayState.IDLE, BirthdayState.CHECK_BOOKING)
        assert sm.can_transition(BirthdayState.ASK_DATE, BirthdayState.ASK_KIDS)
        assert sm.can_transition(BirthdayState.ASK_KIDS, BirthdayState.CONFIRM_PHONE)
        assert sm.can_transition(BirthdayState.ASK_KIDS, BirthdayState.ASK_PHONE)
        assert sm.can_transition(BirthdayState.ASK_FORMAT, BirthdayState.ASK_TIME)
    
    def test_invalid_transitions(self):
        """Verify invalid transitions are detected."""
        sm = BirthdayStateMachine("test_user_123", "telegram")
        
        # These should be invalid
        assert not sm.can_transition(BirthdayState.IDLE, BirthdayState.COMPLETE)
        assert not sm.can_transition(BirthdayState.ASK_DATE, BirthdayState.ASK_EXTRAS)
        assert not sm.can_transition(BirthdayState.COMPLETE, BirthdayState.ASK_DATE)


class TestBirthdayTriggers:
    """Tests for birthday trigger words."""
    
    def test_direct_triggers(self):
        """Test direct trigger phrases."""
        assert contains_birthday_trigger("хочу забронировать день рождения")
        assert contains_birthday_trigger("Отпраздновать праздник")
        assert contains_birthday_trigger("именинник будет рад")
        assert contains_birthday_trigger("ребенку исполняется 5 лет")
    
    def test_case_insensitive(self):
        """Triggers should be case-insensitive."""
        assert contains_birthday_trigger("ДЕНЬ РОЖДЕНИЯ")
        assert contains_birthday_trigger("День Рождения")
        assert contains_birthday_trigger("BIRTHDAY")
    
    def test_no_false_positives(self):
        """Messages without triggers should return False."""
        assert not contains_birthday_trigger("Сколько стоит билет?")
        assert not contains_birthday_trigger("Как добраться до парка?")
        assert not contains_birthday_trigger("Меню ресторана")
    
    def test_triggers_count(self):
        """Verify we have enough triggers."""
        assert len(BIRTHDAY_TRIGGERS) >= 15, "Should have at least 15 trigger phrases"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
