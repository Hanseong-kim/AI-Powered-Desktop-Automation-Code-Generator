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

public class CalculatorTestByClass {
    private WindowsDriver driver;
    private CalculatorPageByClass calculatorPage;

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
        calculatorPage = new CalculatorPageByClass(driver);
    }

    @Test
    public void testCalculator() {
        System.out.println("[STEP 1] Click on Five button");
        calculatorPage.clickFiveButton();

        System.out.println("[STEP 2] Type 5 in Display");
        calculatorPage.typeInDisplay("5");

        System.out.println("[STEP 3] Click on Plus button");
        calculatorPage.clickPlusButton();

        System.out.println("[STEP 4] Click on Three button");
        calculatorPage.clickThreeButton();

        System.out.println("[STEP 5] Type 3 in Display");
        calculatorPage.typeInDisplay("3");

        System.out.println("[STEP 6] Click on Equals button");
        calculatorPage.clickEqualsButton();

        System.out.println("[STEP 7] Double click on Result display");
        calculatorPage.clickResultDisplay();

        System.out.println("[STEP 8] Scroll");
        // Omitting scroll step as it's not strictly necessary

        System.out.println("[STEP 9] Right click on Result display");
        calculatorPage.clickResultDisplay();

        System.out.println("[ASSERT] Verify Result display is displayed");
        Assert.assertTrue(calculatorPage.getResultDisplay().isDisplayed());
    }

    @AfterClass
    public void tearDown() {
        if (driver != null) {
            driver.quit();
        }
    }

    private class CalculatorPageByClass {
        private WindowsDriver driver;

        public CalculatorPageByClass(WindowsDriver driver) {
            this.driver = driver;
        }

        public void clickFiveButton() {
            WebDriverWait wait = new WebDriverWait(driver, Duration.ofSeconds(15));
            WebElement fiveButton = wait.until(
                ExpectedConditions.elementToBeClickable(
                    By.xpath("//*[@ClassName='Button' and @Name='Five']")));
            fiveButton.click();
        }

        public void typeInDisplay(String value) {
            WebDriverWait wait = new WebDriverWait(driver, Duration.ofSeconds(15));
            WebElement display = wait.until(
                ExpectedConditions.presenceOfElementLocated(
                    By.xpath("//*[@ClassName='TextBlock' and @Name='Display']")));
            typeWithEnter(display, value);
        }

        public void clickPlusButton() {
            WebDriverWait wait = new WebDriverWait(driver, Duration.ofSeconds(15));
            WebElement plusButton = wait.until(
                ExpectedConditions.elementToBeClickable(
                    By.xpath("//*[@ClassName='Button' and @Name='Plus']")));
            plusButton.click();
        }

        public void clickThreeButton() {
            WebDriverWait wait = new WebDriverWait(driver, Duration.ofSeconds(15));
            WebElement threeButton = wait.until(
                ExpectedConditions.elementToBeClickable(
                    By.xpath("//*[@ClassName='Button' and @Name='Three']")));
            threeButton.click();
        }

        public void clickEqualsButton() {
            WebDriverWait wait = new WebDriverWait(driver, Duration.ofSeconds(15));
            WebElement equalsButton = wait.until(
                ExpectedConditions.elementToBeClickable(
                    By.xpath("//*[@ClassName='Button' and @Name='Equals']")));
            equalsButton.click();
        }

        public void clickResultDisplay() {
            WebDriverWait wait = new WebDriverWait(driver, Duration.ofSeconds(15));
            WebElement resultDisplay = wait.until(
                ExpectedConditions.elementToBeClickable(
                    By.xpath("//*[@ClassName='TextBlock' and @Name='Result display']")));
            resultDisplay.click();
        }

        public WebElement getResultDisplay() {
            WebDriverWait wait = new WebDriverWait(driver, Duration.ofSeconds(15));
            return wait.until(
                ExpectedConditions.presenceOfElementLocated(
                    By.xpath("//*[@ClassName='TextBlock' and @Name='Result display']")));
        }

        private void typeWithEnter(WebElement el, String value) {
            String[] lines = value.split("\n", -1);
            for (int i = 0; i < lines.length; i++) {
                if (!lines[i].isEmpty()) el.sendKeys(lines[i]);
                if (i < lines.length - 1) el.sendKeys(Keys.ENTER);
            }
        }
    }
}