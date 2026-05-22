package com.settlement;

import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.*;

class AppTest {

    @Test
    void exitCode1ForInvalidDateFormat() {
        int code = App.run(new String[]{"--date", "2026/05/25", "--rule", "T1"});
        assertEquals(1, code);
    }

    @Test
    void exitCode1ForInvalidRule() {
        int code = App.run(new String[]{"--date", "2026-05-25", "--rule", "T4"});
        assertEquals(1, code);
    }

    @Test
    void exitCode1ForMissingDate() {
        int code = App.run(new String[]{"--rule", "T1"});
        assertEquals(1, code);
    }

    @Test
    void exitCode1ForMissingRule() {
        int code = App.run(new String[]{"--date", "2026-05-25"});
        assertEquals(1, code);
    }

    @Test
    void exitCode0ForHelpFlag() {
        int code = App.run(new String[]{"--help"});
        assertEquals(0, code);
    }
}
