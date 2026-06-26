package com.qaforge.tests;

import java.net.MalformedURLException;
import java.net.URL;
import java.time.Duration;
import org.openqa.selenium.By;
import org.openqa.selenium.Keys;
import org.openqa.selenium.WebElement;
import org.openqa.selenium.support.ui.ExpectedConditions;
import org.openqa.selenium.support.ui.WebDriverWait;
import org.testng.Assert;
import org.testng.annotations.AfterClass;
import org.testng.annotations.BeforeClass;
import org.testng.annotations.Test;
import io.appium.java_client.AppiumBy;
import io.appium.java_client.windows.WindowsDriver;
import io.appium.java_client.windows.options.WindowsOptions;

public class CalculatorTestById {
    private WindowsDriver driver;
    private CalculatorPageById page;

    class CalculatorPageById {
        private WebElement fiveButton;
        private WebElement display;
        private WebElement plusButton;
        private WebElement threeButton;
        private WebElement equalsButton;
        private WebElement resultDisplay;

        public void clickFiveButton() {
            fiveButton = new WebDriverWait(driver, Duration.ofSeconds(15)).until(ExpectedConditions.elementToBeClickable(AppiumBy.accessibilityId("num5Button")));
            fiveButton.click();
        }

        public void typeDisplay(String value) {
            display = new WebDriverWait(driver, Duration.ofSeconds(15)).until(ExpectedConditions.presenceOfElementLocated(AppiumBy.accessibilityId("CalculatorResults")));
            typeWithEnter(display, value);
        }

        public void clickPlusButton() {
            plusButton = new WebDriverWait(driver, Duration.ofSeconds(15)).until(ExpectedConditions.elementToBeClickable(AppiumBy.accessibilityId("plusButton")));
            plusButton.click();
        }

        public void clickThreeButton() {
            threeButton = new WebDriverWait(driver, Duration.ofSeconds(15)).until(ExpectedConditions.elementToBeClickable(AppiumBy.accessibilityId("num3Button")));
            threeButton.click();
        }

        public void clickEqualsButton() {
            equalsButton = new WebDriverWait(driver, Duration.ofSeconds(15)).until(ExpectedConditions.elementToBeClickable(AppiumBy.accessibilityId("equalButton")));
            equalsButton.click();
        }

        public void clickResultDisplay() {
            resultDisplay = new WebDriverWait(driver, Duration.ofSeconds(15)).until(ExpectedConditions.elementToBeClickable(AppiumBy.accessibilityId("CalculatorResults")));
            resultDisplay.click();
        }

        private void typeWithEnter(WebElement el, String value) {
            String[] lines = value.split("\n", -1);
            for (int i = 0; i < lines.length; i++) {
                if (!lines[i].isEmpty()) el.sendKeys(lines[i]);
                if (i < lines.length - 1) el.sendKeys(Keys.ENTER);
            }
        }
    }

    @BeforeClass
    public void setUp() throws Exception {
        new ProcessBuilder("").start();

        WindowsOptions desktopOpts = new WindowsOptions();
        desktopOpts.setApp("Root");
        WindowsDriver desktopDriver = new WindowsDriver(new URL("http://127.0.0.1:4723"), desktopOpts);
        WebDriverWait desktopWait = new WebDriverWait(desktopDriver, Duration.ofSeconds(15));
        WebElement appWindow = desktopWait.until(
                ExpectedConditions.presenceOfElementLocated(
                        By.xpath("//Window[contains(@Name,'Calculator')]")));
        String hexHandle = "0x" + Long.toHexString(Long.parseLong(appWindow.getAttribute("NativeWindowHandle")));
        desktopDriver.quit();

        WindowsOptions options = new WindowsOptions();
        options.setCapability("appTopLevelWindow", hexHandle);
        driver = new WindowsDriver(new URL("http://127.0.0.1:4723"), options);
        page = new CalculatorPageById();
    }

    @Test
    public void testCalculator() {
        System.out.println("[STEP 1] Click Five Button");
        page.clickFiveButton();

        System.out.println("[STEP 2] Type Display");
        page.typeDisplay("5");

        System.out.println("[STEP 3] Click Plus Button");
        page.clickPlusButton();

        System.out.println("[STEP 4] Click Three Button");
        page.clickThreeButton();

        System.out.println("[STEP 5] Type Display");
        page.typeDisplay("3");

        System.out.println("[STEP 6] Click Equals Button");
        page.clickEqualsButton();

        System.out.println("[STEP 7] Click Result Display");
        page.clickResultDisplay();

        System.out.println("[STEP 9] Click Result Display");
        page.clickResultDisplay();

        System.out.println("[STEP 10] Verify Result Display is displayed");
        Assert.assertTrue(page.resultDisplay.isDisplayed());
    }

    @AfterClass
    public void tearDown() {
        if (driver != null) {
            driver.quit();
        }
    }
}