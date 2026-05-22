package com.settlement.model;

public enum SettlementRule {
    T1(1),
    T2(2),
    T3(3);

    private final int days;

    SettlementRule(int days) {
        this.days = days;
    }

    public int days() {
        return days;
    }

    public static SettlementRule fromString(String s) {
        return valueOf(s.toUpperCase());
    }
}
