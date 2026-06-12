package com.qaforge.tests;

import io.appium.java_client.AppiumBy;
import io.appium.java_client.windows.WindowsDriver;
import io.appium.java_client.windows.options.WindowsOptions;
import java.net.MalformedURLException;
import java.net.URL;
import java.time.Duration;
import org.openqa.selenium.WebElement;
import org.openqa.selenium.support.ui.ExpectedConditions;
import org.openqa.selenium.support.ui.WebDriverWait;
import org.testng.Assert;
import org.testng.annotations.AfterClass;
import org.testng.annotations.BeforeClass;
import org.testng.annotations.Test;

class CalculatorPageById {
    private WindowsDriver driver;
    private WebDriverWait wait;

    CalculatorPageById(WindowsDriver driver, WebDriverWait wait) {
        this.driver = driver;
        this.wait = wait;
    }

    WebElement fiveButton() {
        return wait.until(ExpectedConditions.elementToBeClickable(AppiumBy.accessibilityId("num5Button")));
    }

    WebElement plusButton() {
        return wait.until(ExpectedConditions.elementToBeClickable(AppiumBy.accessibilityId("plusButton")));
    }

    WebElement threeButton() {
        return wait.until(ExpectedConditions.elementToBeClickable(AppiumBy.accessibilityId("num3Button")));
    }

    WebElement equalsButton() {
        return wait.until(ExpectedConditions.elementToBeClickable(AppiumBy.accessibilityId("equalButton")));
    }

    WebElement resultsDisplay() {
        return wait.until(ExpectedConditions.visibilityOfElementLocated(AppiumBy.accessibilityId("CalculatorResults")));
    }

    void clickFive() { fiveButton().click(); }
    void clickPlus() { plusButton().click(); }
    void clickThree() { threeButton().click(); }
    void clickEquals() { equalsButton().click(); }
}

public class CalculatorTestById {
    private WindowsDriver driver;
    private CalculatorPageById page;

    @BeforeClass
    public void setUp() throws MalformedURLException {
        WindowsOptions options = new WindowsOptions();
        options.setApp("C:\\Windows\\System32\\calc.exe");
        driver = new WindowsDriver(new URL("http://127.0.0.1:4723"), options);
        WebDriverWait wait = new WebDriverWait(driver, Duration.ofSeconds(15));
        page = new CalculatorPageById(driver, wait);
    }

    @Test
    public void testCalculatorAddition() {
        System.out.println("[STEP 1] Click digit 5");
        page.clickFive();

        System.out.println("[STEP 2] Click plus operator");
        page.clickPlus();

        System.out.println("[STEP 3] Click digit 3");
        page.clickThree();

        System.out.println("[STEP 4] Click equals");
        page.clickEquals();

        System.out.println("[STEP 5] Verify result display is visible");
        WebElement result = page.resultsDisplay();
        Assert.assertTrue(result.isDisplayed(), "Result display should be visible after calculation");
    }

    @AfterClass
    public void tearDown() {
        if (driver != null) driver.quit();
    }
}
