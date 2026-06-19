package com.qaforge.tests;

import java.net.MalformedURLException;
import java.net.URL;
import java.time.Duration;
import org.openqa.selenium.By;
import org.openqa.selenium.WebElement;
import org.openqa.selenium.interactions.Actions;
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

    class CalculatorPageByClass {
        private By fiveButtonLocator = By.className("Button");
        private By displayLocator = By.className("TextBlock");
        private By plusButtonLocator = By.className("Button");
        private By threeButtonLocator = By.className("Button");
        private By equalsButtonLocator = By.className("Button");
        private By resultDisplayLocator = By.className("TextBlock");
        private By applicationFrameWindowLocator = By.className("ApplicationFrameWindow");

        public void clickFiveButton() {
            WebDriverWait wait = new WebDriverWait(driver, Duration.ofSeconds(15));
            WebElement fiveButton = wait.until(ExpectedConditions.elementToBeClickable(fiveButtonLocator));
            fiveButton.click();
        }

        public void typeDisplay(String value) {
            WebDriverWait wait = new WebDriverWait(driver, Duration.ofSeconds(15));
            WebElement display = wait.until(ExpectedConditions.presenceOfElementLocated(displayLocator));
            display.clear();
            display.sendKeys(value);
        }

        public void clickPlusButton() {
            WebDriverWait wait = new WebDriverWait(driver, Duration.ofSeconds(15));
            WebElement plusButton = wait.until(ExpectedConditions.elementToBeClickable(plusButtonLocator));
            plusButton.click();
        }

        public void clickThreeButton() {
            WebDriverWait wait = new WebDriverWait(driver, Duration.ofSeconds(15));
            WebElement threeButton = wait.until(ExpectedConditions.elementToBeClickable(threeButtonLocator));
            threeButton.click();
        }

        public void clickEqualsButton() {
            WebDriverWait wait = new WebDriverWait(driver, Duration.ofSeconds(15));
            WebElement equalsButton = wait.until(ExpectedConditions.elementToBeClickable(equalsButtonLocator));
            equalsButton.click();
        }

        public void doubleClickResultDisplay() {
            WebDriverWait wait = new WebDriverWait(driver, Duration.ofSeconds(15));
            WebElement resultDisplay = wait.until(ExpectedConditions.presenceOfElementLocated(resultDisplayLocator));
            Actions actions = new Actions(driver);
            actions.doubleClick(resultDisplay).perform();
        }

        public void scrollApplicationFrameWindow(String value) {
            WebDriverWait wait = new WebDriverWait(driver, Duration.ofSeconds(15));
            WebElement applicationFrameWindow = wait.until(ExpectedConditions.presenceOfElementLocated(applicationFrameWindowLocator));
            Actions actions = new Actions(driver);
            actions.moveToElement(applicationFrameWindow).scrollBy(0, Integer.parseInt(value)).perform();
        }

        public void rightClickResultDisplay() {
            WebDriverWait wait = new WebDriverWait(driver, Duration.ofSeconds(15));
            WebElement resultDisplay = wait.until(ExpectedConditions.presenceOfElementLocated(resultDisplayLocator));
            Actions actions = new Actions(driver);
            actions.contextClick(resultDisplay).perform();
        }
    }

    @BeforeClass
    public void setUp() throws Exception {
        new ProcessBuilder("C:\\Windows\\System32\\calc.exe").start();

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
        calculatorPage = new CalculatorPageByClass();
    }

    @Test
    public void testCalculator() {
        System.out.println("[STEP 1] Click Five Button");
        calculatorPage.clickFiveButton();

        System.out.println("[STEP 2] Type Display");
        calculatorPage.typeDisplay("5");

        System.out.println("[STEP 3] Click Plus Button");
        calculatorPage.clickPlusButton();

        System.out.println("[STEP 4] Click Three Button");
        calculatorPage.clickThreeButton();

        System.out.println("[STEP 5] Type Display");
        calculatorPage.typeDisplay("3");

        System.out.println("[STEP 6] Click Equals Button");
        calculatorPage.clickEqualsButton();

        System.out.println("[STEP 7] Double Click Result Display");
        calculatorPage.doubleClickResultDisplay();

        System.out.println("[STEP 8] Scroll Application Frame Window");
        calculatorPage.scrollApplicationFrameWindow("-3");

        System.out.println("[STEP 9] Right Click Result Display");
        calculatorPage.rightClickResultDisplay();

        WebDriverWait wait = new WebDriverWait(driver, Duration.ofSeconds(15));
        WebElement resultDisplay = wait.until(ExpectedConditions.presenceOfElementLocated(calculatorPage.resultDisplayLocator));
        Assert.assertTrue(resultDisplay.isDisplayed());
    }

    @AfterClass
    public void tearDown() {
        driver.quit();
    }
}